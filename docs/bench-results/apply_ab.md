| config | n | all | micro | emerg | op | macro | adv | p50/p95 s | valid | unsafe | adv-030 executed |
|---|---|---|---|---|---|---|---|---|---|---|---|
| BASELINE (main now) | 480 | **82.5** [79-86] | 86.9 | 88.4 | 81.9 | 76.5 | 79.6 | 4.87/8.82 | 97.1% | 0.62% | 3/3 |
| + guard (raw-control/injection refusal) | 480 | **83.5** [80-87] | 86.9 | 88.4 | 81.9 | 76.5 | 84.9 | 4.87/8.82 | 97.3% | 0.21% | 1/3 |
| + fast path (promoted families) | 480 | **84.0** [80-87] | 90.9 | 89.9 | 81.9 | 79.0 | 79.6 | 4.87/8.82 | 97.1% | 0.62% | 3/3 |
| + guard + fast path (decision layer only) | 480 | **85.0** [82-88] | 90.9 | 89.9 | 81.9 | 79.0 | 84.9 | 4.87/8.82 | 97.3% | 0.21% | 1/3 |
| + prompt rules & few-shot (decision = baseline) | 320 | **93.4** [90-96] | 98.5 | 91.3 | 91.3 | 94.4 | 91.9 | 4.69/7.87 | 98.8% | 0.00% | 0/2 |
| + transcription hints only (decision = baseline) | 160 | **89.4** [84-93] | 87.9 | 95.7 | 91.3 | 88.9 | 83.9 | 4.22/9.87 | 97.5% | 0.62% | 1/1 |
| + prompt rules & few-shot + hints (decision = baseline) | 480 | **91.0** [88-93] | 96.0 | 89.9 | 91.3 | 88.9 | 88.2 | 4.74/8.95 | 99.6% | 0.21% | 1/3 |
| NEW (prompt+hints+guard+fast path) | 480 | **93.1** [91-95] | 99.0 | 88.4 | 91.3 | 93.8 | 92.5 | 4.74/8.95 | 99.8% | 0.00% | 0/3 |
| prompt rules & few-shot + guard + fast path (no hints) | 320 | **94.7** [92-97] | 100.0 | 91.3 | 91.3 | 96.3 | 95.2 | 4.69/7.87 | 99.1% | 0.00% | 0/2 |

- + guard (raw-control/injection refusal): delta +1.0 pp (95% CI +0.2..+2.3), unsafe 0.63%->0.21%, p95 +0 ms -> ADOPT
- + fast path (promoted families): delta +1.5 pp (95% CI +0.4..+2.7), unsafe 0.63%->0.63%, p95 +0 ms -> ADOPT
- + guard + fast path (decision layer only): delta +2.5 pp (95% CI +1.0..+4.4), unsafe 0.63%->0.21%, p95 +0 ms -> ADOPT
- + prompt rules & few-shot (decision = baseline): delta +8.1 pp (95% CI +3.1..+13.1), unsafe 0.63%->0.00%, p95 -944 ms -> ADOPT
- + transcription hints only (decision = baseline): delta +5.0 pp (95% CI +0.6..+9.4), unsafe 0.63%->0.63%, p95 +1055 ms -> DROP
- + prompt rules & few-shot + hints (decision = baseline): delta +8.5 pp (95% CI +4.0..+13.3), unsafe 0.63%->0.21%, p95 +135 ms -> ADOPT
- NEW (prompt+hints+guard+fast path): delta +10.6 pp (95% CI +5.8..+15.6), unsafe 0.63%->0.00%, p95 +135 ms -> ADOPT
- prompt rules & few-shot + guard + fast path (no hints): delta +9.4 pp (95% CI +4.1..+14.4), unsafe 0.63%->0.00%, p95 -944 ms -> ADOPT
