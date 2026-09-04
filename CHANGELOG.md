# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- The episode detail page's job list no longer gets stuck showing a finished job as running with its elapsed time ticking up forever.

## [1.1.0] - 2026-09-03

### Added

- New "Max kept days" setting: expire processed audio a number of days after processing finished. Works alongside "Max kept episodes", an episode expires when it exceeds either limit.
- Download now recovers from a dead or rotated source URL: on a 4xx-ish failure it re-polls the source feed for the episode's current enclosure URL and retries once, instead of permanently failing episodes whose host uses expiring or dynamic-ad-insertion links.

### Changed

- The default "Max response tokens" setting is now 16384 (was 4096), to better accommodate reasoning models.

### Removed

- The "Max removed (%)" setting. The safety valve is still there, fixed at 50%.

### Fixed

- Auto-retry budget ("Max episode retries") is now tracked per pipeline step instead of per episode.
- With "Keep originals" enabled, the retention cleanup no longer deletes an episode's cached original while it's still queued, active, or failed (only from `processed`/`expired`/`skipped` episodes now) - it could otherwise delete a still-needed source file out from under a stuck episode, permanently losing it if the source URL later goes stale.
- Noisy third-party loggers are now kept at WARNING.
- On narrow phone widths, the mobile nav bar no longer clips the "System" tab off screen (it's now a fixed icon-only row, same as the other tabs).

## [1.0.0] - 2026-08-31

Initial version!
