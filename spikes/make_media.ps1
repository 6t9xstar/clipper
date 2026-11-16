# Generates license-free synthetic test speech with Windows SAPI. Output: media/speech.wav + media/reference.txt
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech
$out = Join-Path $PSScriptRoot "media"
New-Item -ItemType Directory -Force $out | Out-Null
$paragraphs = @(
 "Welcome back to the show. Today we are talking about how small teams can ship software faster without burning out.",
 "So, here is the thing. Most teams do not have a speed problem. They have a focus problem.",
 "Why do so many projects fail? Because nobody writes down what done actually means. That single habit changes everything.",
 "Let me give you an example. Last year we cut our release time from six weeks to five days, just by limiting work in progress.",
 "And then we moved on to the budget discussion, which honestly took most of the afternoon.",
 "Um, okay, so, where was I. Right. The meeting notes. We will share those later.",
 "Here is the most important lesson of this entire episode. Never deploy on a Friday unless you can roll back in under one minute.",
 "Did you know that the average developer switches tasks more than twenty times a day? That is a huge hidden cost.",
 "Anyway, thanks for listening. If this helped you, share it with one person on your team."
)
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$synth.Rate = 0
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$synth.SetOutputToWaveFile((Join-Path $out "speech.wav"), $fmt)
$pb = New-Object System.Speech.Synthesis.PromptBuilder
foreach ($p in $paragraphs) { $pb.AppendText($p); $pb.AppendBreak([TimeSpan]::FromMilliseconds(1800)) }
$synth.Speak($pb)
$synth.Dispose()
$paragraphs -join "`n" | Set-Content -Encoding utf8 (Join-Path $out "reference.txt")
