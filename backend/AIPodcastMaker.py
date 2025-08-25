# Import used libs
import json
from blib.apis.onepw import get_openai_api_key
from blib.termio.terminal import ColorOut, Spinner
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
    
    # Script generation
    def generate_script(
            self,
            topic: str,
            length: str,
            key_points: list[str] = []
    ) -> str:
        # Load prompts
        system_prompt = self.__load_asset('scripts/ai_script_gen_prompt.md', {"LEN_DEF_WORD_ENGLISH": length.lower()})
        user_prompt = self.__load_asset('scripts/user_prompt.md', {"PODCAST_TOPIC": topic})

        # Tack on user special key points
        if len(key_points) > 0:
            user_prompt += "\nMake sure to cover all these key points in the podcast:"
            for point in key_points:
                user_prompt = user_prompt + f"\n- {point}"

        # Generate prompt
        with Spinner("Generating podcast"):
            script = self.__get_api_response(system_prompt=system_prompt, user_message=user_prompt, model='gpt-4o-mini', temp=0.2)

        # Validate prompt and save to class instance
        self.json_serialized_script: list[dict[str, dict[str, str] | str]] = self.__validate_script(script)

        # Return just in case
        return script
    
def test():
    podcast = AIPodcastMaker()

    script = podcast.generate_script(
        topic="What computer to buy for college",
        length='very long',
        key_points=[
            "Mac vs PC",
            "Size considerations",
            "New v.s. Used"
        ]
    )

    print(script)

if __name__ == '__main__':
    if input('run demo? ') == 'y':
        test()
