param(
    [ValidateSet("baseline", "doubao", "deepseek", "kimi", "minimax", "foundry")]
    [string]$Provider = "deepseek",

    [switch]$WithGreeting,
    [switch]$PrintConfig
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

function Import-DotEnv {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        Write-Host "Missing .env. Copy .env.example to .env and fill in your keys first." -ForegroundColor Yellow
        exit 1
    }

    Get-Content -LiteralPath $Path | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) {
            return
        }
        $parts = $line.Split("=", 2)
        $name = $parts[0].Trim()
        $value = $parts[1].Trim().Trim('"').Trim("'")
        [Environment]::SetEnvironmentVariable($name, $value, "Process")
    }
}

function Get-ProviderValue {
    param(
        [string]$ProviderName,
        [string]$Suffix,
        [string]$DefaultValue
    )
    $key = "BYOM_$($ProviderName.ToUpperInvariant())_$Suffix"
    $value = [Environment]::GetEnvironmentVariable($key, "Process")
    if ($value) {
        return $value
    }
    return $DefaultValue
}

Import-DotEnv -Path (Join-Path $PSScriptRoot ".env")

$defaults = @{
    baseline = @{ ModelType = "gpt-5.4"; Endpoint = "https://<your-foundry-resource>.cognitiveservices.azure.com/openai/v1"; Auth = "api-key" }
    foundry  = @{ ModelType = "gpt-5.4"; Endpoint = "https://<your-foundry-resource>.cognitiveservices.azure.com/openai/v1"; Auth = "api-key" }
    doubao   = @{ ModelType = "doubao-seed-2-0-lite-260428"; Endpoint = "https://ark.cn-beijing.volces.com/api/v3"; Auth = "bearer" }
    deepseek = @{ ModelType = "DeepSeek-V4-Flash"; Endpoint = "https://<your-foundry-resource>.services.ai.azure.com/openai/v1/"; Auth = "bearer" }
    kimi     = @{ ModelType = "Kimi-K2.6"; Endpoint = "https://<your-foundry-resource>.services.ai.azure.com/openai/v1/"; Auth = "bearer" }
    minimax  = @{ ModelType = "MiniMax-M2.7"; Endpoint = "https://api.minimaxi.com/v1"; Auth = "bearer" }
}

$preset = $defaults[$Provider]
$modelType = Get-ProviderValue -ProviderName $Provider -Suffix "MODEL_TYPE" -DefaultValue $preset.ModelType
$endpoint = Get-ProviderValue -ProviderName $Provider -Suffix "ENDPOINT" -DefaultValue $preset.Endpoint
$authScheme = [Environment]::GetEnvironmentVariable("BYOM_AUTH_SCHEME", "Process")
if (-not $authScheme) {
    $authScheme = $preset.Auth
}

$python = Get-Command py -ErrorAction SilentlyContinue
if ($python) {
    $pythonCommand = @("py")
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $pythonCommand = @("python")
} else {
    Write-Host "Python was not found on PATH. Install Python 3.10+ or start from the web console on a configured machine." -ForegroundColor Red
    exit 1
}

$argsList = @(
    "byom_demo.py",
    "--provider", $Provider,
    "--model", $modelType,
    "--byom", "byom-chat-completion",
    "--byom-endpoint", $endpoint,
    "--byom-model-type", $modelType,
    "--byom-auth-scheme", $authScheme,
    "--verbose"
)

if (-not $WithGreeting) {
    $argsList += "--no-proactive-greeting"
}

if ($PrintConfig) {
    $argsList += "--print-config"
}

Write-Host "Running Voice Live BYOM demo" -ForegroundColor Cyan
Write-Host "Provider: $Provider"
Write-Host "Model/deployment: $modelType"
Write-Host "Endpoint: $endpoint"
Write-Host "Auth: $authScheme"

& $pythonCommand[0] @argsList
