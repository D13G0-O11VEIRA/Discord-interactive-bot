# Integração com Minecraft

Adiciona ao bot: status do servidor, painel que se atualiza sozinho, aviso quando o servidor
liga/desliga, `/mc start`, whitelist/console por RCON e **ponte de chat** nos dois sentidos.
Nada do código existente foi alterado: o `bot.py` já carrega tudo que está em `cogs/`.

## Arquivos novos

```
cogs/minecraft.py              comandos /mc, monitor de status e ponte
services/minecraft/            ping de status, RCON, parser de log, transportes, controladores
companion/DiscordBridge/       plugin Paper/Spigot (Java) que liga o servidor ao bot
tests/                         testes automatizados (46)
.env.example                   variáveis novas
```

Não há dependência nova (usa `aiohttp`, que já vem com o discord.py).
Adicione `data/` ao `.gitignore` (o bot guarda ali o canal da ponte e o painel).

## Aternos: o que dá e o que não dá

* O Aternos **não tem API oficial** e os Termos de Uso proíbem automatizar o site. A biblioteca
  `python-aternos` está arquivada desde fev/2024 e o próprio autor diz que o Aternos passou a
  detectar requisições automáticas e bloquear com Cloudflare. Por isso **o bot não clica em
  "Iniciar" por você**.
* `/mc start` no modo `aternos`: mostra um botão para o painel, e o bot **avisa no canal (mencionando
  quem pediu) assim que o endereço ficar online**. Também funciona o aviso geral de "servidor online".
* Quer que `/mc start` ligue de verdade? Hospede o servidor na própria máquina (`MC_PROVIDER=local`)
  ou numa hospedagem com painel/API. O controlador é uma classe pequena
  (`services/minecraft/controllers.py`); dá para criar outra para Pterodactyl etc.
* Sem acesso a log nem RCON no Aternos, a ponte de chat precisa do plugin (abaixo).

## Configuração rápida

1. Copie os arquivos para o repositório e preencha o `.env` (veja `.env.example`). O mínimo é `MC_ADDRESS`.
2. Convide o bot com o escopo `applications.commands` e ative **Message Content Intent** no Developer Portal
   (a ponte lê as mensagens do canal).
3. `python bot.py`, depois `/mc configurar ponte:#canal avisos:#canal cargo:@Minecraft`.
4. No Aternos, rode `/mc raw` com o servidor **desligado** e depois **ligado**: ele mostra o que o
   endereço responde. Se as palavras forem diferentes de `offline`/`starting`..., ajuste
   `MC_OFFLINE_HINTS` e `MC_TRANSITION_HINTS`.

## Comandos

| Comando | Quem | O que faz |
|---|---|---|
| `/mc status`, `/mc jogadores`, `/mc ip` | todos | estado, jogadores, endereço |
| `/mc start` | todos (30 s de espera por usuário) | liga (local) ou abre o painel (Aternos) e avisa quando ficar online |
| `/mc stop` | Gerenciar Servidor | desliga (local, via RCON) |
| `/mc painel` | Gerenciar Servidor | mensagem de status que se atualiza sozinha |
| `/mc configurar` | Gerenciar Servidor | canal da ponte, canal de avisos, cargo a mencionar |
| `/mc ponte`, `/mc raw` | `ponte`: todos; `raw`: Gerenciar Servidor | diagnóstico |
| `/mc whitelist`, `/mc cmd` | Gerenciar Servidor | via RCON; `cmd` só com `MC_ALLOW_CONSOLE=true` |

## Ponte de chat

### Modo `plugin` (Aternos e qualquer hospedagem que aceite plugins)

1. Gere o jar (precisa de JDK 17+ e Gradle): na pasta `companion/DiscordBridge` rode `gradle build`.
   O arquivo sai em `build/libs/DiscordBridge-1.0.0.jar`.
2. Envie o jar para a pasta `plugins` do servidor (no Aternos: aba de arquivos/plugins; o servidor
   precisa ser Paper ou Spigot, 1.18+).
3. Inicie uma vez, edite `plugins/DiscordBridge/config.yml` (`url` e `token`) e reinicie.
4. No `.env` do bot: `MC_BRIDGE_MODE=plugin` e `MC_WS_TOKEN=` (o mesmo token, 16+ caracteres).

**O plugin precisa alcançar o bot pela internet.** O plugin só faz conexões de saída, mas o bot
escuta em `MC_WS_PORT`. Se o bot roda no seu PC, use um túnel (Cloudflare Tunnel, ngrok...) ou
hospede o bot num servidor com IP público; use `wss://` sempre que houver HTTPS. Endereços de túnel
rápido costumam mudar a cada execução.

Se preferir não compilar nada, o plugin **DiscordSRV** faz a ponte sozinho (ele mesmo fala com o
Discord); nesse caso deixe `MC_BRIDGE_MODE=off` e use o bot só para status/avisos.

### Modo `local` (servidor na mesma máquina do bot)

`MC_BRIDGE_MODE=local`, `MC_SERVER_DIR` (ou `MC_LOG_FILE`) e RCON ativo no `server.properties`
(`enable-rcon=true`, `rcon.port`, `rcon.password`). O bot lê o `latest.log` e responde com `/tellraw`.
Mortes são detectadas por frases conhecidas (heurística); o modo `plugin` envia a mensagem exata.

### Protocolo do plugin (JSON por WebSocket, `/ws`)

Plugin → bot: `hello` (com `token`), `chat`, `join`, `leave`, `death`, `advancement`, `players`,
`server_started`, `server_stopping`. Bot → plugin: `chat` (`author`, `message`).

## Segurança

* Menções (`@everyone`) são bloqueadas nos dois sentidos; o `§` é removido para ninguém injetar cores.
* O token do WebSocket é comparado em tempo constante; sem `hello` válido em 10 s a conexão cai.
* RCON não é criptografado: mantenha a porta restrita à própria máquina.
* `/mc cmd` executa qualquer comando no console. Deixe `MC_ALLOW_CONSOLE=false` se não precisar.

## Testes

```
python -m unittest discover -s tests -t . -v
```
