# Detection evals

Compare LLMs on how well they find ads, scored against episodes you have checked by hand. Everything happens on the **Evals** page: build the labeled set, run models over it, and compare the results. A command line exists too, for testing changes to hushcast's own detection code before deploying them.

## 1. Label episodes

On an episode page, fix the detected segments first: mark false positives "Not an ad", add missed ads from the transcript, and trim block edges.

Some audio has no right answer: a network ident, a "you're listening to" bumper, a "coming up after the break" teaser, closing credits with a plug in them. Select those lines in the transcript and choose **Mark as optional**. An optional range is fine to cut and fine to keep, so models are not scored on it. It works on its own or over part of a detected segment, it is not a correction (nothing is cut and it never feeds "Distill hints"), and **Remove** in the segments table takes it away again. Use it for judgment calls only. A real ad or real content still needs a real answer.

Then use the **Eval label** panel:

- **Verify segments** saves the segments as the episode's ground truth. The label keeps its own copy of the transcript, cues and duration, so it survives retention cleanup and re-detection.
- **Exclude** keeps an episode out of the eval set, with a reason. Use it for episodes that can't serve as truth, such as a transcript with silence or another feed's ads in it.
- **Next unlabeled episode** jumps to the next older episode of the same podcast that still needs a label.

The panel only appears on episodes whose detection has finished. If the segments change after you verify, it tells you which case you are in. After your own corrections it offers **Save again**. After a re-detect the segments are the new model's unchecked output, so it asks you to confirm before **Replace ground truth** overwrites what you verified. Either way, evals keep using the saved copy until you act.

Expired episodes can be labeled too. Their transcript file is gone, so the episode page shows a transcript rebuilt from the stored detection prompt: the same lines the model saw, without word timestamps.

The **Dataset** tab shows the whole set: totals, coverage per podcast with how many episodes are left to label, and every label with flags for out-of-date labels, rebuilt transcripts and hint leakage (see below).

A good set covers each feed and each kind of break (pre-roll, mid-roll, host-read, inserted, post-roll), plus a few episodes with no ads and a promo-only one if you have them. Aim for at least 30 to 60 verified episodes. More is better: a larger set gives steadier scores and makes small differences between models easier to trust. The cost is that every run detects every verified episode once per repeat.

## 2. Run models

On the **Runs** tab:

- **Eval connection** is where runs send their requests. Left blank, it is the production connection from Settings. Set a dedicated API key here to have your gateway report eval spend separately from regular detection.
- **Models** are what you compare. A model reachable through the eval connection only needs a name and its model ID. A model on another provider takes its own base URL and key. Optional extras: temperature, extra request parameters (for example reasoning effort), and manual token prices.
- **Run** the selected models. Before it starts you see how many detections and roughly how many input tokens the run involves. Each episode is detected once per repeat (default 3), because a model's output varies between calls.

A key is never sent to a different endpoint than the one it was entered for: a model with its own base URL uses its own key or none.

Runs continue in the background, one at a time, alongside regular processing. Every detection is saved as it finishes, so a cancelled, failed or interrupted run can be resumed and only makes the detections that are missing. After a server restart, unfinished runs wait for you to resume them. Eval calls are kept apart from an episode's own LLM call history.

### Cost

Hushcast has no price list. It records the cost the provider reports with each response (OpenRouter-style `usage.cost`, or a LiteLLM proxy's `x-litellm-response-cost` header). A reported zero counts as free. If a provider reports nothing, the model's manual prices are used when set, and otherwise the cost is unknown: shown as a dash, and left out of the cost chart.

## 3. Compare results

The **Results** tab picks one run per model by default, and only runs that cover the whole verified set as it is today. Only episodes every compared run has results for are scored, so an older run made before you added labels would shrink the comparison for all of them. Such a run stays in the list, marked with how many episodes it covers, and you can tick it yourself. When no run covers the whole set, for example right after you verify a new episode, the runs with the best coverage are picked. Your own selection is remembered in the browser until a new run has results.

| metric | meaning |
| --- | --- |
| content cut | seconds of non-ad audio removed, per hour of audio. The worse error. |
| ad missed | seconds of ad audio left in, per hour |
| weighted error | ad missed + content weight × content cut. The single ranking number. |
| blunder episodes | episodes with more than the blunder threshold of content cut |
| ad blocks found | truth blocks at least half covered by a cut |
| spurious blocks | cuts mostly outside any ad |
| start / end error | how far found blocks' edges land from the truth. A negative start or positive end cuts content. |
| optional ranges cut | share of the optional audio a model removed. Shown for information: it is not part of any other number, and neither direction is better. |
| outcome correct | cut vs promo-only. A circuit-breaker review counts as wrong. |
| re-detected | share of detections that had a segment over the max segment duration and were run again, as the pipeline does. The retry's cost is included, and a model that needs it often is less reliable even when its scores look fine. |

Scoring controls re-score everything in place, since runs store predictions and not scores:

- **Content weight** (default 3): how many seconds of missed ad one second of cut content is worth. Try a few values and see whether the ranking changes.
- **Collar** (default 1 s): ignored on each side of every ground-truth boundary, since labels and transcript line edges are only accurate to about a second.
- **Blunder** (default 5 s): the content cut in one episode that counts as a blunder.

Optional ranges are left out the same way as the collar: cutting one is not content cut, keeping one is not a missed ad, and a cut that covers only optional audio is not a spurious block. Ad ranges separated only by optional audio count as one block.

Below the table, the cost versus error chart shows which models are worth their price, and the per-episode table opens a timeline of where each run's cuts fell against the ground truth. Filter that table by podcast, or sort it to bring up the episodes a model did worst on or the ones the models disagree on most. **Use this model** makes a model the one production detection uses.

Results follow the labels. Correcting a label's ground truth re-scores every run. If an episode's transcript copy changes, earlier results for it are left out until the model is run again. Runs made under different hints or detection settings are flagged, because differences between them may not come from the models.

Promo-only episodes are scored on the outcome only. A failed detection (the model never returned usable JSON) counts as cutting nothing. Every run is finalized exactly like the pipeline does it: snapping, merging, cue bridging, refinement, the promo check, the circuit breakers, and the one re-detection after an overlong segment.

## Hints and leakage

Runs use the hints production uses today (or none, if you pick that). Learned hints are distilled from your corrections, so when a labeled episode's corrections went into the current hints, the hints partly describe that episode's answer and flatter every model on it. Those episodes are flagged "in hints", and Results can leave them out. If the ranking holds without them, the leak doesn't change your decision. Episodes you verify without correcting anything never feed the distiller.

## Command line: testing code changes

The browser runs the detection code that is deployed. To check a change to prompt assembly or post-processing before deploying it, run the eval from a checkout, which uses that checkout's code:

```bash
uv run python -m hushcast.evals fetch http://your-hushcast:4874    # writes .evals/dataset.json, one read-only request
uv run python -m hushcast.evals run --variant variants/glm.toml
uv run python -m hushcast.evals summarize --per-episode
```

With login enabled, pass your session cookie to `fetch`: `--cookie "hushcast_session=..."`. The Dataset tab's Download dataset button gives you the same file. It holds transcripts, so keep it private. `.evals/` is gitignored.

A variant is a TOML file. The API key comes from an environment variable, never from the file:

```toml
name = "glm-5.3-flash"
api_key_env = "OPENROUTER_API_KEY"

[settings]              # llm_base_url and llm_model are required, any other hushcast setting is optional
llm_base_url = "https://openrouter.ai/api/v1"
llm_model = "z-ai/glm-5.3-flash"

[extra_params]          # optional, added to the request body as-is
reasoning = { effort = "low" }

[pricing]               # optional fallback when the provider reports no cost, USD per million tokens
input_per_mtok = 0.10
output_per_mtok = 0.40
```

`run` options: `--repeats` (default 3), `--concurrency`, `--episodes 12,40`, `--hints none`. Responses are cached in `.evals/cache` by request and repeat number, so re-running only pays for calls not made yet. `summarize` with no arguments compares the newest report of each variant in `.evals/reports`, and takes `--content-weight`, `--collar` and `--blunder` like the Results tab. Run the same variant before and after your change, and compare the two reports.
