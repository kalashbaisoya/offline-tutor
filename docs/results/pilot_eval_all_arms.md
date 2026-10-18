# Pilot evaluation

Fresh quiz questions written by the teacher; none appear in the training data. Score 0-2 per item.
- **held-out**: sections never trained on (does fine-tuning hurt generalisation?)
- **trained**: new questions on trained sections (did the model learn the material?)
- **Useful** = the content score, counted only if the reply is in the student's language/script.

| Model | Split | n | Content | Useful | Numerical | Concept | EN | Hinglish | HI | Right language | Cut off |
|---|---|---|---|---|---|---|---|---|---|---|---|
| base | heldout | 23 | 52% | **22%** | 60% | 50% | 62% | 50% | 40% | 35% | 5 |
| base-hint | heldout | 23 | 63% | **50%** | 80% | 58% | 62% | 65% | 60% | 78% | 3 |
| base-hint-rag | heldout | 23 | 63% | **50%** | 80% | 58% | 56% | 70% | 60% | 78% | 1 |
| base-strict | heldout | 23 | 67% | **37%** | 80% | 64% | 62% | 65% | 80% | 61% | 3 |
| tutor-pilot | heldout | 23 | 30% | **30%** | 40% | 28% | 44% | 30% | 10% | 96% | 0 |
| tutor-pilot-rag | heldout | 23 | 33% | **30%** | 20% | 36% | 25% | 35% | 40% | 91% | 0 |
| tutor-v3 | heldout | 23 | 37% | **33%** | 60% | 31% | 56% | 30% | 20% | 96% | 0 |
| base | train | 50 | 48% | **25%** | 82% | 38% | 55% | 52% | 25% | 44% | 6 |
| base-hint | train | 50 | 43% | **31%** | 73% | 35% | 40% | 50% | 35% | 86% | 2 |
| base-hint-rag | train | 50 | 81% | **78%** | 91% | 78% | 90% | 90% | 45% | 92% | 1 |
| base-strict | train | 50 | 50% | **32%** | 73% | 44% | 45% | 60% | 40% | 60% | 6 |
| tutor-pilot | train | 50 | 45% | **44%** | 82% | 35% | 57% | 40% | 30% | 96% | 0 |
| tutor-pilot-rag | train | 50 | 58% | **55%** | 73% | 54% | 65% | 70% | 20% | 92% | 0 |
| tutor-v3 | train | 50 | 39% | **38%** | 73% | 29% | 48% | 35% | 30% | 96% | 2 |

| Model | Regurgitation max / mean run (words) |
|---|---|
| base | 6 / 2.3 |
| base-hint | 4 / 2.3 |
| base-hint-rag | 8 / 2.5 |
| base-strict | 4 / 2.3 |
| tutor-pilot | 6 / 2.3 |
| tutor-pilot-rag | 6 / 2.4 |
| tutor-v3 | 4 / 2.1 |

Regurgitation: longest verbatim word run shared with the true 40-word continuation of a training passage (30 probes, greedy). A run of 15+ words would indicate memorised book text.
