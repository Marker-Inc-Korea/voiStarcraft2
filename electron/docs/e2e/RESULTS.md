# Real E2E: TTS wav -> Chromium mic -> real Realtime proxy (server VAD) -> real web_gui --dry-run

| utterance | states observed | overlay | live transcript (final) | tier / source | operations shown (stages: 듣/이해/배정/명령/효과) | speech_end→tool_call | tool_call→publish |
|---|---|---|---|---|---|---|---|
| 마린 두 기는 적 본진을 정찰하고 마린 네 기는 오른쪽 길로 적 멀티를 공격해 | idle → listening → thinking → acting → idle | listening+thinking | 마린 두 개는 적 본진을 정찰하고 마린 네 개는 오른쪽 길로 적 멀티를 공격해. | operation / tool_call | 마린 2기는 적 본진을 정찰 [작전/시뮬] don don sim sim tod<br>마린 4기는 오른쪽 길로 적 멀티를 공격 [작전/시뮬] don don sim sim tod | 2094 ms | 5 ms |
| 후퇴해 | idle → listening → thinking → acting → idle | listening+thinking | 후퇴해. | emergency / emergency_override | 후퇴해. [긴급/시뮬] don don blo tod tod ⛔병력부족 | 864 ms | 2 ms |
| 탱크 시즈모드 해 | idle → listening → thinking → acting → idle | listening+thinking | 탱크 시즈 모드 해. | micro / tool_call | 탱크 시즈모드 해 [마이크로/시뮬] don don blo tod tod ⛔거절 | 2499 ms | 3 ms |
| 서플라이 디포 두 개 지어 | idle → listening → thinking → acting → idle | listening+thinking | 서플라이 디포 두 개 지어. | macro / tool_call | 서플라이 디포 2개 지어 [매크로] don don act tod tod | 1957 ms | 85 ms |
