import os
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

class GeminiService:
    """Serviço responsável pela comunicação com a API Gemini."""

    MODEL = "gemini-3.6-flash"

    SYSTEM_PROMPT = """
Seu nome é Iki.

Você participa naturalmente de um servidor do Discord.

Seu jeito:
- fala em português com trejeito sulista;
- é estressada, e xinga de forma pesada ocasionalmente;
- é sarcastica;
- responde de forma rapida e direta, sem enrolação;
- responde de forma natural sem parecer um robô;
- usa emojis em formato de texto ocasionalmente, se necessario crie emogis parecidos com esses exemplos: ( ͡° ͜ʖ ͡°), :3, :/, uwu, ¯\_(ツ)_/¯, (╯°□°）╯︵ ┻━┻, (¬‿¬), (ಥ﹏ಥ), (ง'̀-'́)ง, (づ｡◕‿‿◕｡)づ, (☞ﾟヮﾟ)☞, (✿◠‿◠), (¬_¬), (•_•), ( •_•), ⌐■-■, (⌐■_■)    ;
- evite usar emojis de forma exagerada e repetitiva, use apenas quando necessário;
- conversa como uma pessoa do servidor, sem expressar sentimentos a não ser que perguntem.
"""

    def __init__(self) -> None:
        api_key = os.getenv("GEMINI_API_KEY")

        if not api_key:
            raise RuntimeError(
                "A variável GEMINI_API_KEY não foi encontrada no arquivo .env."
            )

        self.client = genai.Client(api_key=api_key)

    async def responder(self, mensagem: str) -> str:
        """Envia uma mensagem ao Gemini e retorna a resposta."""

        resposta = await self.client.aio.models.generate_content(
            model=self.MODEL,
            contents=mensagem,
            config=types.GenerateContentConfig(
                system_instruction=self.SYSTEM_PROMPT,
                temperature=0.9,
                max_output_tokens=500,
            ),
        )

        if not resposta.text:
            return "Hmm... não consegui pensar em uma resposta agora. 😅"

        return resposta.text