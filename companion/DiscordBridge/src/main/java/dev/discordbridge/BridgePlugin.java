package dev.discordbridge;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import org.bukkit.Bukkit;
import org.bukkit.ChatColor;
import org.bukkit.entity.Player;
import org.bukkit.event.EventHandler;
import org.bukkit.event.EventPriority;
import org.bukkit.event.Listener;
import org.bukkit.event.entity.PlayerDeathEvent;
import org.bukkit.event.player.AsyncPlayerChatEvent;
import org.bukkit.event.player.PlayerAdvancementDoneEvent;
import org.bukkit.event.player.PlayerJoinEvent;
import org.bukkit.event.player.PlayerQuitEvent;
import org.bukkit.plugin.java.JavaPlugin;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.WebSocket;
import java.nio.ByteBuffer;
import java.time.Duration;
import java.util.concurrent.CompletionStage;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;

/**
 * Liga o chat do servidor ao bot de Discord por WebSocket.
 *
 * O plugin só faz conexões de SAÍDA (nada precisa ser aberto no servidor), por isso
 * funciona em hospedagens como o Aternos. Protocolo: veja docs/MINECRAFT.md.
 */
public final class BridgePlugin extends JavaPlugin implements Listener {

    private static final int MAX_TEXT = 256;

    private final ScheduledExecutorService executor = Executors.newSingleThreadScheduledExecutor(task -> {
        Thread thread = new Thread(task, "DiscordBridge-io");
        thread.setDaemon(true);
        return thread;
    });
    private final HttpClient http = HttpClient.newHttpClient();
    private final AtomicBoolean reconnectPending = new AtomicBoolean(false);

    private volatile WebSocket socket;
    private volatile boolean stopping = false;
    private volatile boolean announcedStart = false;
    private volatile int retryDelaySeconds = 2;

    private String url;
    private String token;
    private boolean sendJoins;
    private boolean sendDeaths;
    private boolean sendAdvancements;

    // ------------------------------------------------------------------ ciclo de vida
    @Override
    public void onEnable() {
        saveDefaultConfig();
        url = getConfig().getString("url", "").trim();
        token = getConfig().getString("token", "").trim();
        sendJoins = getConfig().getBoolean("send-joins", true);
        sendDeaths = getConfig().getBoolean("send-deaths", true);
        sendAdvancements = getConfig().getBoolean("send-advancements", false);

        if (!(url.startsWith("ws://") || url.startsWith("wss://")) || token.isEmpty()
                || token.equals("troque-este-token")) {
            getLogger().severe("Configure 'url' (ws:// ou wss://) e 'token' em plugins/DiscordBridge/config.yml.");
            getServer().getPluginManager().disablePlugin(this);
            return;
        }

        getServer().getPluginManager().registerEvents(this, this);
        connect();
    }

    @Override
    public void onDisable() {
        stopping = true;
        executor.shutdownNow();
        WebSocket ws = socket;
        socket = null;
        if (ws != null) {
            try {
                JsonObject goodbye = new JsonObject();
                goodbye.addProperty("type", "server_stopping");
                ws.sendText(goodbye.toString(), true).get(2, TimeUnit.SECONDS);
                ws.sendClose(WebSocket.NORMAL_CLOSURE, "bye").get(2, TimeUnit.SECONDS);
            } catch (Exception ignored) {
                // o servidor está desligando; não vale a pena insistir
            }
        }
    }

    // ------------------------------------------------------------------ conexão
    private void connect() {
        if (stopping) {
            return;
        }
        final URI uri;
        try {
            uri = URI.create(url);
        } catch (IllegalArgumentException e) {
            getLogger().severe("URL inválida em config.yml: " + url);
            return;
        }
        http.newWebSocketBuilder()
                .connectTimeout(Duration.ofSeconds(10))
                .buildAsync(uri, new BridgeListener())
                .whenComplete((ws, error) -> {
                    if (error != null) {
                        getLogger().warning("Não consegui conectar ao bot (" + rootMessage(error)
                                + "). Nova tentativa em " + retryDelaySeconds + "s.");
                        scheduleReconnect();
                        return;
                    }
                    socket = ws;
                    retryDelaySeconds = 2;
                    getLogger().info("Conectado ao bot.");
                    JsonObject hello = new JsonObject();
                    hello.addProperty("type", "hello");
                    hello.addProperty("token", token);
                    hello.addProperty("server", Bukkit.getName());
                    send(hello);
                    Bukkit.getScheduler().runTask(this, () -> {
                        sendPlayers(null);
                        if (!announcedStart) {
                            announcedStart = true;
                            simple("server_started");
                        }
                    });
                });
    }

    private void scheduleReconnect() {
        if (stopping || !reconnectPending.compareAndSet(false, true)) {
            return;
        }
        final int delay = retryDelaySeconds;
        retryDelaySeconds = Math.min(retryDelaySeconds * 2, 60);
        try {
            executor.schedule(() -> {
                reconnectPending.set(false);
                connect();
            }, delay, TimeUnit.SECONDS);
        } catch (RuntimeException e) {
            // executor já encerrado (servidor desligando)
        }
    }

    private static String rootMessage(Throwable error) {
        Throwable cause = error;
        while (cause.getCause() != null) {
            cause = cause.getCause();
        }
        return cause.getClass().getSimpleName() + (cause.getMessage() != null ? ": " + cause.getMessage() : "");
    }

    private final class BridgeListener implements WebSocket.Listener {
        private final StringBuilder buffer = new StringBuilder();

        @Override
        public void onOpen(WebSocket webSocket) {
            webSocket.request(1);
        }

        @Override
        public CompletionStage<?> onText(WebSocket webSocket, CharSequence data, boolean last) {
            buffer.append(data);
            if (last) {
                String message = buffer.toString();
                buffer.setLength(0);
                handleIncoming(message);
            }
            webSocket.request(1);
            return null;
        }

        @Override
        public CompletionStage<?> onPing(WebSocket webSocket, ByteBuffer message) {
            webSocket.request(1);
            try {
                webSocket.sendPong(message);
            } catch (IllegalStateException ignored) {
                // já existe um pong em andamento
            }
            return null;
        }

        @Override
        public CompletionStage<?> onClose(WebSocket webSocket, int statusCode, String reason) {
            getLogger().warning("Conexão com o bot encerrada (" + statusCode + " " + reason + ").");
            if (socket == webSocket) {
                socket = null;
            }
            scheduleReconnect();
            return null;
        }

        @Override
        public void onError(WebSocket webSocket, Throwable error) {
            getLogger().warning("Erro na conexão com o bot: " + rootMessage(error));
            if (socket == webSocket) {
                socket = null;
            }
            scheduleReconnect();
        }
    }

    // ------------------------------------------------------------------ envio (jogo -> bot)
    /** Os envios passam por uma única thread: o WebSocket do Java não aceita dois sendText simultâneos. */
    private void send(JsonObject json) {
        final WebSocket ws = socket;
        if (ws == null || stopping) {
            return;
        }
        final String text = json.toString();
        try {
            executor.execute(() -> {
                try {
                    ws.sendText(text, true).join();
                } catch (RuntimeException e) {
                    getLogger().fine("Falha ao enviar ao bot: " + e.getMessage());
                }
            });
        } catch (RuntimeException e) {
            // executor encerrado
        }
    }

    private void simple(String type) {
        JsonObject json = new JsonObject();
        json.addProperty("type", type);
        send(json);
    }

    private void playerEvent(String type, String player, String key, String value) {
        JsonObject json = new JsonObject();
        json.addProperty("type", type);
        json.addProperty("player", player);
        if (key != null) {
            json.addProperty(key, value);
        }
        send(json);
    }

    private void sendPlayers(String excluded) {
        JsonArray names = new JsonArray();
        for (Player online : Bukkit.getOnlinePlayers()) {
            if (!online.getName().equals(excluded)) {
                names.add(online.getName());
            }
        }
        JsonObject json = new JsonObject();
        json.addProperty("type", "players");
        json.add("players", names);
        send(json);
    }

    @EventHandler(priority = EventPriority.MONITOR, ignoreCancelled = true)
    public void onChat(AsyncPlayerChatEvent event) {
        playerEvent("chat", event.getPlayer().getName(), "message", event.getMessage());
    }

    @EventHandler(priority = EventPriority.MONITOR)
    public void onJoin(PlayerJoinEvent event) {
        if (sendJoins) {
            playerEvent("join", event.getPlayer().getName(), null, null);
        }
        sendPlayers(null);
    }

    @EventHandler(priority = EventPriority.MONITOR)
    public void onQuit(PlayerQuitEvent event) {
        if (sendJoins) {
            playerEvent("leave", event.getPlayer().getName(), null, null);
        }
        sendPlayers(event.getPlayer().getName());
    }

    @EventHandler(priority = EventPriority.MONITOR)
    public void onDeath(PlayerDeathEvent event) {
        if (!sendDeaths) {
            return;
        }
        String message = event.getDeathMessage();
        String name = event.getEntity().getName();
        playerEvent("death", name, "message", message != null ? message : name + " morreu");
    }

    @EventHandler(priority = EventPriority.MONITOR)
    public void onAdvancement(PlayerAdvancementDoneEvent event) {
        if (!sendAdvancements) {
            return;
        }
        String key = event.getAdvancement().getKey().getKey();
        if (key.startsWith("recipes/")) {
            return;
        }
        playerEvent("advancement", event.getPlayer().getName(), "advancement", key);
    }

    // ------------------------------------------------------------------ recebimento (bot -> jogo)
    private void handleIncoming(String raw) {
        try {
            JsonElement parsed = JsonParser.parseString(raw);
            if (!parsed.isJsonObject()) {
                return;
            }
            JsonObject json = parsed.getAsJsonObject();
            if (!"chat".equals(text(json, "type"))) {
                return;
            }
            String author = clean(text(json, "author"));
            String message = clean(text(json, "message"));
            if (author.isEmpty() || message.isEmpty()) {
                return;
            }
            final String line = ChatColor.BLUE + "[Discord] " + ChatColor.WHITE + author
                    + ChatColor.GRAY + ": " + message;
            Bukkit.getScheduler().runTask(this, () -> Bukkit.broadcastMessage(line));
        } catch (RuntimeException e) {
            getLogger().fine("Mensagem do bot ignorada: " + e.getMessage());
        }
    }

    private static String text(JsonObject json, String key) {
        JsonElement value = json.get(key);
        return value == null || value.isJsonNull() ? "" : value.getAsString();
    }

    /** Remove '§' (códigos de cor vindos do Discord) e caracteres de controle. */
    private static String clean(String input) {
        String cleaned = input.replace('\u00a7', ' ').replaceAll("[\\p{Cntrl}]", " ").replaceAll("\\s+", " ").trim();
        return cleaned.length() > MAX_TEXT ? cleaned.substring(0, MAX_TEXT) : cleaned;
    }
}
