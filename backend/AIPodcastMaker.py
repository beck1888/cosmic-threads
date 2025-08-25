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

# Paths and constants
ASSETS_DIR = Path("backend/assets")
SOUNDS_DIR = ASSETS_DIR / "sounds"
TMP_DIR = Path("tmp")
FINAL_DIR = Path("final podcasts")

INTRO_SOUND = SOUNDS_DIR / "intro.mp3"
OUTRO_SOUND = SOUNDS_DIR / "outro.mp3"

VOICE_BY_HOST = {"jake": "echo", "luna": "shimmer"}
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
        if openai_api_key:
            self.openai_api_key = openai_api_key
        else:
            self.openai_api_key = get_openai_api_key()

        self._client = OpenAI(api_key=self.openai_api_key)
        self.__go_prompt: str = ""
        self.json_serialized_script: List[
            Dict[str, Union[Dict[str, str], str]]
        ] = []

    def __load_asset(
        self, asset_name: str, swaps: Dict[str, str] = {}
    ) -> str:
        asset_path = ASSETS_DIR / asset_name
        with asset_path.open("r", encoding="UTF-8") as f:
            asset_text = f.read()
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
        try:
            return json.loads(script_to_validate)
        except json.JSONDecodeError:
            return False

    def __speak_text(self, host: str, text: str) -> str:
        use_voice = VOICE_BY_HOST.get(host.lower(), DEFAULT_TTS_VOICE)
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        file_name = TMP_DIR / f"{uuid4()}.mp3"

        self._client.audio.speech.create(
            voice=use_voice,
            model=TTS_MODEL,
            input=text,
            response_format="mp3",
            speed=TTS_SPEED,
        ).write_to_file(str(file_name))

        return str(file_name)

    def generate_script(
        self, topic: str, length: str, key_points: List[str] = []
    ) -> None:
        self.__go_prompt = topic + "Focus on: " + "".join(key_points)

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

        if key_points:
            numbered = "\n".join(
                f"{i}. {point}" for i, point in enumerate(key_points, start=1)
            )
            user_prompt += (
                "\nMake sure to cover all these key points in the podcast:\n"
                + numbered
            )

        script = self.__get_api_response(
            system_prompt=system_prompt,
            user_message=user_prompt,
            model=DEFAULT_CHAT_MODEL,
            temp=DEFAULT_TEMP,
        )

        validated_script = self.__validate_script(script)
        if validated_script is False:
            raise ValueError("Script parsing error. Invalid JSON.")
        self.json_serialized_script = validated_script

    def create_audio(self) -> Path:
        script = self.json_serialized_script
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        FINAL_DIR.mkdir(parents=True, exist_ok=True)

        clips: List[str] = [str(INTRO_SOUND)]
        temp_clips: List[str] = []

        for tool_call in script:
            tool_name = tool_call.get("tool_name")
            params = tool_call.get("tool_params", {})

            if tool_name == "speak":
                speaker = params.get("speaker", "")
                spoken_content = params.get("text", "")
                temp_clip = self.__speak_text(
                    host=speaker, text=spoken_content
                )
                clips.append(temp_clip)
                temp_clips.append(temp_clip)

            elif tool_name == "sfx":
                sfx = params.get("sound", "")
                if sfx:
                    clips.append(f"backend/assets/sounds/sfx/{sfx}.mp3")

        clips.append(str(OUTRO_SOUND))

        audio = CompiledAudioDriver()
        for clip in clips:
            try:
                audio.add_clip(clip)
            except FileNotFoundError:
                pass
        audio.compile()

        compiled_path = TMP_DIR / f"{uuid4()}.mp3"
        audio.save_compiled_audio(str(compiled_path))

        newName = self.__get_api_response(
            system_prompt=(
                "You are an expert in concise file renaming. Based on this "
                "script idea, come up with a 3 word name for the final "
                "podcast title. Output only the final podcast title, no "
                "other characters, no file extensions."
            ),
            user_message=self.__go_prompt,
            model="gpt-4o",
        )

        newName = self.__sanitize_filename(newName)
        final_path = FINAL_DIR / f"{newName}.mp3"
        shutil.move(str(compiled_path), str(final_path))

        for clip in temp_clips:
            try:
                os.remove(clip)
            except Exception:
                pass

        return final_path

    @staticmethod
    def __sanitize_filename(name: str) -> str:
        return "".join(c for c in name if c.isalnum() or c in " _-").strip()


def main():
    clr = ColorOut()
    clr.blue("Welcome to the AI Podcast Maker!\n")

    topic = input("Enter a topic for your podcast: ").strip()
    subtopics = input(
        "Enter subtopics to include (comma separated, optional): "
    ).strip()
    length = input(
        "How long do you want your podcast (short/medium/long)? "
    ).strip()

    podcast = AIPodcastMaker()

    with Spinner("Creating podcast. This may take a few minutes."):
        podcast.generate_script(
            topic=topic,
            length=length,
            key_points=subtopics.split(",") if subtopics else [],
        )
        final_location = podcast.create_audio()

    clr.green("\nPodcast created successfully!")
    clr.blue(f"You can find it here: {final_location}\n")


if __name__ == "__main__":
    main()