import json
import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Union
from uuid import uuid4

from blib.apis.onepw import get_openai_api_key
from blib.audio.compiled_audio_driver import CompiledAudioDriver
from blib.termio.terminal import ColorOut, Spinner
from openai import OpenAI

# Debug
EMPTY_DICT: dict = {}

# Paths and constants
ASSETS_DIR = Path("backend/assets")
SOUNDS_DIR = ASSETS_DIR / "sounds"
TMP_DIR = Path("tmp")
FINAL_DIR = Path("final podcasts")

INTRO_SOUND = SOUNDS_DIR / "intro.mp3"
OUTRO_SOUND = SOUNDS_DIR / "outro.mp3"

# Host to voice mapping for TTS
VOICE_BY_HOST = {
    "jake": "echo",
    "luna": "shimmer",
}

DEFAULT_TTS_VOICE = "onyx"
TTS_MODEL = "gpt-4o-mini-tts"
TTS_SPEED = 1.13

DEFAULT_CHAT_MODEL = "gpt-4o-mini"
DEFAULT_TEMP = 0.2


class AIPodcastMaker:
    """
    End-to-end helper for generating a podcast script with OpenAI,
    converting it to audio clips, and assembling a final podcast file.
    """

    def __init__(self, openai_api_key: Optional[str] = None) -> None:
        self._clr = ColorOut()

        if openai_api_key:
            self.openai_api_key = openai_api_key
        else:
            self._clr.yellow("Warning: No API key given. Fetching from 1PW.")
            self.openai_api_key = get_openai_api_key()

        # Initialize OpenAI client once per instance
        self._client = OpenAI(api_key=self.openai_api_key)

        # Will be set by generate_script
        self.__go_prompt: str = ""
        self.json_serialized_script: List[
            Dict[str, Union[Dict[str, str], str]]
        ] = []

    def __load_asset(
        self, asset_name: str, swaps: Dict[str, str] = EMPTY_DICT
    ) -> str:
        """
        Load a text asset and optionally perform ${KEY} replacements.
        """
        asset_path = ASSETS_DIR / asset_name
        with asset_path.open("r", encoding="UTF-8") as f:
            asset_text = f.read()

        # Perform template replacements
        if swaps:
            for key, value in swaps.items():
                asset_text = asset_text.replace(r"${{" + key + r"}}", value)

        return asset_text

    def __get_api_response(
        self,
        system_prompt: str,
        user_message: str,
        model: str,
        temp: Optional[float] = None,
    ) -> str:
        """
        Send a chat completion request and return the assistant's text.
        """
        reply = self._client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=temp if temp is not None else DEFAULT_TEMP,
        )
        return reply.choices[0].message.content

    def __validate_script(
        self, script_to_validate: str
    ) -> Union[List[Dict[str, Union[Dict[str, str], str]]], bool]:
        """
        Validate that the script is valid JSON. Returns parsed JSON or False.
        """
        try:
            parsed = json.loads(script_to_validate)
            self._clr.green("Json format is okay")
        except json.JSONDecodeError:
            return False

        # TODO: Validate tool calls/format/length structure more deeply
        return parsed

    def __speak_text(self, host: str, text: str) -> str:
        """
        Generate a speech audio clip for the given host and text.
        Returns the path to the generated mp3 file.
        """
        # Choose a voice, fallback to default if unknown host
        use_voice = VOICE_BY_HOST.get(host.lower(), DEFAULT_TTS_VOICE)
        if host.lower() not in VOICE_BY_HOST:
            self._clr.red(
                f"Unknown voice '{host}'. Falling back to '{DEFAULT_TTS_VOICE}'."
            )

        # Ensure output directory exists
        TMP_DIR.mkdir(parents=True, exist_ok=True)

        file_name = TMP_DIR / f"{uuid4()}.mp3"

        # Generate the clip
        self._client.audio.speech.create(
            voice=use_voice,
            model=TTS_MODEL,
            input=text,
            response_format="mp3",
            speed=TTS_SPEED,  # Slightly faster but natural
        ).write_to_file(str(file_name))

        return str(file_name)

    def generate_script(
        self, topic: str, length: str, key_points: List[str] = []
    ) -> str:
        """
        Generate a podcast script JSON string based on topic, length, and key points.
        Also stores parsed script in the instance for later audio generation.
        """
        # Save info to class (kept original construction to avoid changing behavior)
        self.__go_prompt = topic + "Focus on: " + "".join(key_points)

        # Load prompts
        system_prompt = self.__load_asset(
            "scripts/ai_script_gen_prompt.md",
            {"LEN_DEF_WORD_ENGLISH": length.lower()},
        )
        user_prompt = self.__load_asset(
            "scripts/user_prompt.md",
            {
                "PODCAST_TOPIC": topic,
                "LEN_DEF_WORD_ENGLISH": length.lower(),
            },
        )

        # Append key points if provided
        if key_points:
            numbered = "\n".join(
                f"{i}. {point}" for i, point in enumerate(key_points, start=1)
            )
            user_prompt += (
                "\nMake sure to cover all these key points in the podcast:\n"
                + numbered
            )

        # Generate script via API
        with Spinner("Generating podcast"):
            script = self.__get_api_response(
                system_prompt=system_prompt,
                user_message=user_prompt,
                model=DEFAULT_CHAT_MODEL,
                temp=DEFAULT_TEMP,
            )

        # Validate and store
        validated_script = self.__validate_script(script)

        if validated_script is False:
            self._clr.red(
                "Script parsing error. Script failed to parse (fatal)."
            )
            exit(1)
        else:
            self.json_serialized_script = validated_script  # type: ignore[assignment]

        return script

    def create_audio(self) -> None:
        """
        Execute the script tool calls to produce audio clips, assemble them,
        and save the final podcast file with a concise generated name.
        """
        script = self.json_serialized_script

        # Ensure output directories exist
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        FINAL_DIR.mkdir(parents=True, exist_ok=True)

        # Track clips
        clips: List[str] = [str(INTRO_SOUND)]
        temp_clips: List[str] = []  # Temporary files to delete later

        total_tool_calls = len(script)
        print(f"Found {total_tool_calls} tool calls.")

        # Execute tool calls
        for i, tool_call in enumerate(script, start=1):
            tool_name = tool_call.get("tool_name")  # type: ignore[assignment]
            params = tool_call.get("tool_params", {})  # type: ignore[assignment]

            # Speak tool
            if tool_name == "speak":
                speaker = params.get("speaker", "")
                spoken_content = params.get("text", "")
                print(f"{speaker.capitalize()}: {spoken_content}")

                with Spinner(f"Calling tool ({i}/{total_tool_calls})"):
                    temp_clip = self.__speak_text(
                        host=speaker, text=spoken_content
                    )
                    clips.append(temp_clip)
                    temp_clips.append(temp_clip)
            # Sound effect tool
            elif tool_name == "sfx":
                sfx = params.get("sound", "")
                if not sfx == "":
                    print(f"Adding SFX: {sfx}")
                    clips.append(f'backend/assets/sounds/sfx/{sfx}.mp3')

        # Add outro
        clips.append(str(OUTRO_SOUND))

        # Assemble all clips
        audio = CompiledAudioDriver()
        for clip in clips:
            audio.add_clip(clip)
        audio.compile()

        compiled_path = TMP_DIR / f"{uuid4()}.mp3"
        audio.save_compiled_audio(str(compiled_path))
        print(f"Compiled audio saved to: {compiled_path}")

        # Clean up temporary clips
        for clip in temp_clips:
            try:
                os.remove(clip)
            except FileNotFoundError:
                print(f"Warning: Temporary file {clip} not found for cleanup.")
            except Exception as e:
                print(f"Error while deleting temporary file {clip}: {e}")

        # Generate new podcast name
        with Spinner("Renaming file"):
            newName = self.__get_api_response(
                system_prompt=(
                    "You are an expert in concise file renaming. Based on this "
                    "script idea, come up with a 3 word name for the final "
                    "podcast title. Output only the final podcast title, no "
                    "other characters, no file extensions. Use 3 short words "
                    "max. You will now read the podcast's inspiration prompt."
                ),
                user_message=self.__go_prompt,
                model="gpt-4o",
            )

        # Sanitize file name
        newName = self.__sanitize_filename(newName)

        # Move final podcast to its own folder
        try:
            final_path = FINAL_DIR / f"{newName}.mp3"
            shutil.move(str(compiled_path), str(final_path))
            print(f"Final podcast saved to: {final_path}")
        except Exception as e:
            print(f"Error while moving final podcast: {e}")

    @staticmethod
    def __sanitize_filename(name: str) -> str:
        """
        Sanitize a string for safe filesystem use: keep alnum, space, _, -.
        """
        return "".join(c for c in name if c.isalnum() or c in " _-").strip()


def test():
    podcast = AIPodcastMaker()

    script = podcast.generate_script(
        topic="Why you feel weird when going down in an elevator",
        length="medium",
        key_points=[
            "What the feeling is",
            "F=ma",
            "Why F=ma connects to you feeling less heavy"
        ]
    )

    print(script)
    podcast.create_audio()


if __name__ == "__main__":
    if input("run demo? ") == "y":
        test()