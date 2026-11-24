- Transcription works with providers that return one block of text plus word timings, such as ElevenLabs
  Scribe through OpenRouter. Lines are built from the word timings (word timestamps must be on), and music
  the provider reports is used as audio cues for ad detection.
- New "Max part length" transcription setting sends long episodes in parts, for providers that time out on
  long audio.
- Speaker labels from providers that number speakers from 0 are no longer dropped.
