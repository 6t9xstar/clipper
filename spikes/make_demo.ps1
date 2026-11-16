# Original two-host demo "podcast" voiced with Windows SAPI (no third-party content).
# Output: media/demo/lNN_<voice>.wav, one per line, + lines.tsv
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Speech
$dir = Join-Path $PSScriptRoot "media\demo"
New-Item -ItemType Directory -Force $dir | Out-Null
$lines = @(
 "D|Welcome to Small Wins, the show about building better habits. I'm David.",
 "Z|And I'm Zira. Today we're answering one question. Why do most new year's resolutions fail by February?",
 "D|Honestly, I think people aim way too big. They go from zero workouts to six workouts a week.",
 "Z|Right. And here's the surprising part. Research on habits suggests it takes around two months for a new behavior to feel automatic, not twenty one days.",
 "D|Wait, so the twenty one day rule is a myth?",
 "Z|Pretty much. It comes from an old observation about patients adjusting to their new faces after surgery. It was never about habits at all.",
 "D|That's wild. Okay, so, um, what was I going to say. Oh, right. Let's talk about the morning routine stuff.",
 "Z|Sure. But first, a quick reminder to check the show notes for links.",
 "D|Here's my number one tip. Make the new habit so small that it feels almost silly. Two pushups. One page of a book. That's it.",
 "Z|I love that. Because the goal in week one isn't fitness. The goal is to become the kind of person who shows up.",
 "D|Exactly. And never miss twice. If you skip a day, fine. But never skip two days in a row.",
 "Z|Never miss twice. That might be the most useful rule we've ever shared on this show.",
 "D|So, anyway, we also got an email about standing desks, which, uh, we'll probably cover next time, maybe.",
 "Z|Here's a question for you. What if you stacked the new habit on top of something you already do every day?",
 "D|Like doing two pushups right after brushing your teeth?",
 "Z|Exactly. You already brush your teeth twice a day. So the reminder is built in. You never have to remember.",
 "D|Okay, that's genuinely clever. I'm trying that tomorrow.",
 "Z|That's all for today. If this helped, share it with one friend who's starting something new.",
 "D|See you next week on Small Wins."
)
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(44100, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$tsv = @()
for ($i = 0; $i -lt $lines.Count; $i++) {
  $who, $text = $lines[$i].Split("|", 2)
  $voice = if ($who -eq "D") { "Microsoft David Desktop" } else { "Microsoft Zira Desktop" }
  $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
  $s.SelectVoice($voice); $s.Rate = 1
  $f = Join-Path $dir ("l{0:D2}_{1}.wav" -f $i, $who)
  $s.SetOutputToWaveFile($f, $fmt); $s.Speak($text); $s.Dispose()
  $tsv += "$f`t$who`t$text"
}
$tsv | Set-Content -Encoding utf8 (Join-Path $dir "lines.tsv")
