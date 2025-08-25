# Import used libs
import os
import json
from uuid import uuid4
from blib.apis.onepw import get_openai_api_key
from blib.termio.terminal import ColorOut, Spinner
from blib.audio.compiled_audio_driver import CompiledAudioDriver
from openai import OpenAI

# Debug
EMPTY_DICT: dict = {}

# Class
class AIPodcastMaker:
    # Constructor
    def __init__(self, openai_api_key=None):
        if openai_api_key is not None:
            self.openai_api_key = openai_api_key
        else:
            clrOut = ColorOut()
            clrOut.yellow("Warning: No API key given. Fetching from 1PW.")
            self.openai_api_key = get_openai_api_key()

    # Unified asset loading interface
    def __load_asset(self, asset_name: str, swaps: dict[str, str] = EMPTY_DICT) -> str:
        with open(f"backend/assets/{asset_name}", "r", encoding="UTF-8") as f: # If this gives file issues, try taking the "backend" part out
            asset_text = f.read()
        
        # Do swaps
        try:
            for key, value in swaps.items():
                asset_text = asset_text.replace(r"${{" + key + r"}}", value)
        except AttributeError as e:
            pass # Empty dict

        return asset_text

    # Single UI for running OpenAI chat completion queries
    def __get_api_response(
            self,
            system_prompt: str,
            user_message: str,
            model: str,
            temp: float = None
    ) -> str:
        client = OpenAI(api_key=self.openai_api_key)
        reply = client.chat.completions.create(
            model=model,
            messages=[
                {
                    'role': 'system',
                    'content': system_prompt
                },
                {
                    'role': 'user',
                    'content': user_message
                }
            ],
            temperature= temp if temp is not None else 0.2
        )
        return reply.choices[0].message.content
    
    def __validate_script(self, script_to_validate) -> list[dict[str, dict[str, str] | str]]:
        try:
            parsed = json.loads(script_to_validate)
            clr = ColorOut(); clr.green("Json format is okay")
        except SyntaxError as e:
            return False
        
        # TODO: Validate tool calls/ format/ length
        return parsed
    
    def __speak_text(self, host: str, text: str) -> str:
        # Get host voices
        if host.lower() == 'jake':
            voice_instructions = self.__load_asset('voices/jake_voice_style.txt')
            use_voice = 'onyx'
        elif host.lower() == 'luna':
            voice_instructions = self.__load_asset('voices/luna_voice_style.txt')
            use_voice = 'nova'
        else:
            clr = ColorOut()
            clr.red(f"Unknown voice '{host}' resolve and try again.")

        # Name the clip
        file_name = f'tmp/{uuid4()}.mp3'

        # Generate the clip
        client = OpenAI(api_key=self.openai_api_key)
        client.audio.speech.create(
            voice=use_voice,
            instructions=voice_instructions,
            model='gpt-4o-mini-tts',
            input=text,
            response_format='mp3',
            speed=1.05 # Go less slowly, but not oddly fast
        ).write_to_file(file_name)

        # Return the file name so we can keep track of it
        return file_name
    
    # Script generation
    def generate_script(
            self,
            topic: str,
            length: str,
            key_points: list[str] = []
    ) -> str:
        # Load prompts
        system_prompt = self.__load_asset('scripts/ai_script_gen_prompt.md', {"LEN_DEF_WORD_ENGLISH": length.lower()})
        user_prompt = self.__load_asset('scripts/user_prompt.md', {"PODCAST_TOPIC": topic, "LEN_DEF_WORD_ENGLISH": length.lower()})

        # Tack on user special key points
        if len(key_points) > 0:
            user_prompt += "\nMake sure to cover all these key points in the podcast:"
            i = 0
            for point in key_points:
                i += 1
                user_prompt = user_prompt + f"\n{i}. {point}"

        # Debug only
        # print(system_prompt); print(user_prompt); exit(0)

        # Generate prompt
        with Spinner("Generating podcast"):
            script = self.__get_api_response(system_prompt=system_prompt, user_message=user_prompt, model='gpt-4o-mini', temp=0.2)

        # Validate prompt and save to class instance
        validated_script = self.__validate_script(script)
        
        if validated_script is False:
            clr = ColorOut()
            clr.red("Script parsing error. Script failed to parse (fatal).")
            exit(1)
        else:
            # Script is okay to proceed
            self.json_serialized_script: list[dict[str, dict[str, str] | str]] = validated_script

        # Return just in case
        return script
    
    # Process the script into audio chunks (run each tool)
    def create_audio(self):
        # Bring in the script
        script = self.json_serialized_script

        # Create the audio holder folder
        os.makedirs("tmp", exist_ok=True)

        # Remember the audio clips to work with
        clips = []

        # Debug info
        total_tool_calls = str(len(script))
        print(f"Found {total_tool_calls} tool calls.")
        i = 0

        # Call all the tools
        for tool_call in script:
            i += 1
            tool_name = tool_call["tool_name"]
            params = tool_call["tool_params"]

            if tool_name == "speak":
                speaker = params["speaker"]
                spoken_content = params["text"]
                print(f"{speaker.capitalize()}: {spoken_content}")
                with Spinner(f"Calling tool ({str(i)}/{total_tool_calls})"):
                    clips.append(self.__speak_text(
                        host=speaker,
                        text=spoken_content
                    ))

        # Assemble all the clips
        audio = CompiledAudioDriver()
        for clip in clips:
            audio.add_clip(clip)
        audio.compile()
        compiled_clips = f'tmp/{uuid4()}.mp3'
        audio.save_compiled_audio(compiled_clips)
        print(compiled_clips)

def test():
    podcast = AIPodcastMaker()

    script = podcast.generate_script(
        topic="What computer to buy for college",
        length='very short',
        key_points=[
            "Mac vs PC",
            "Size considerations",
            "New v.s. Used"
        ]
    )

    print(script)
    podcast.create_audio()

if __name__ == '__main__':
    if input('run demo? ') == 'y':
        test()
