# DiscordBot

Bot desenvolvido em **Python** utilizando **discord.py**, com foco em Slash Commands, componentes interativos e arquitetura modular usando Cogs.

## Funcionalidades

* Slash Commands (`/ping`, `/avatar`)
* Embeds personalizados
* Arquitetura modular com Cogs
* Integração com variáveis de ambiente (`.env`)
* Carregamento automático de módulos

## Tecnologias

* Python 3.14
* discord.py
* python-dotenv

## Estrutura

```text
DiscordBot/
├── bot.py
├── cogs/
├── .env.example
├── requirements.txt
└── .gitignore
```

## Como executar

```bash
git clone https://github.com/D13G0-O11VEIRA/DiscordBot.git
cd DiscordBot

py -m venv .venv
.\.venv\Scripts\activate

pip install -r requirements.txt
```

Crie um arquivo `.env`

```env
DISCORD_TOKEN=SEU_TOKEN
```

Execute:

```bash
python bot.py
```

## Roadmap

* [x] Slash Commands
* [x] Avatar Command
* [ ] Sistema de boas-vindas
* [ ] Botões interativos
* [ ] Menus suspensos
* [ ] Moderação
* [ ] Integração com IA (Gemini)
