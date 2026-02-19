# APK Android - Project Titan (PoC)

Não foi possível gerar o APK diretamente neste ambiente porque não há toolchain Android instalado (`ANDROID_HOME`/`ANDROID_SDK_ROOT` vazios e sem `buildozer/gradle/flutter`).

## Estrutura pronta

- `main.py`: app mobile PoC (Kivy)
- `buildozer.spec`: configuração de build Android

## Como gerar APK (WSL Ubuntu recomendado)

### Opção automatizada (Windows + PowerShell)

1. Abra PowerShell **como Administrador** e rode:

```powershell
cd F:\botia\project_titan
./scripts/setup_apk_toolchain.ps1
```

2. Depois rode o build:

```powershell
cd F:\botia\project_titan
./scripts/run_build_apk.ps1
```

3. APK esperado:

- `F:\botia\project_titan\mobile\bin\*.apk`

1. Instalar dependências de sistema:

```bash
sudo apt update
sudo apt install -y python3-pip python3-venv openjdk-17-jdk zip unzip git
pip install --upgrade pip
pip install buildozer cython
```

2. Entrar na pasta `mobile` e buildar:

```bash
cd project_titan/mobile
buildozer android debug
```

3. APK gerado em:

- `bin/projecttitanmobile-0.1.0-debug.apk`

## Observação

Este app é um PoC offline para feira e não interage com aplicativos de terceiros.
