plugins {
    java
}

group = "dev.discordbridge"
version = "1.0.0"

repositories {
    mavenCentral()
    maven("https://repo.papermc.io/repository/maven-public/")
}

dependencies {
    // Compila contra a API do 1.20.4 (Java 17): o jar resultante roda do 1.18 em diante.
    compileOnly("io.papermc.paper:paper-api:1.20.4-R0.1-SNAPSHOT")
    // O Gson já vem dentro do servidor; aqui é só para compilar.
    compileOnly("com.google.code.gson:gson:2.10.1")
}

tasks.withType<JavaCompile> {
    options.release.set(17)
    options.encoding = "UTF-8"
}

tasks.processResources {
    filesMatching("plugin.yml") {
        expand("version" to project.version)
    }
}
