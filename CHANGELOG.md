# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- New "Max kept days" setting: expire processed audio a number of days after processing finished. Works alongside "Max kept episodes", an episode expires when it exceeds either limit.

### Changed

- The default "Max response tokens" setting is now 16384 (was 4096), to better accommodate reasoning models.

### Removed

- The "Max removed (%)" setting. The safety valve is still there, fixed at 50%.

### Fixed

- Noisy third-party loggers are now kept at WARNING.
- On narrow phone widths, the mobile nav bar no longer clips the "System" tab off screen (it's now a fixed icon-only row, same as the other tabs).

## [1.0.0] - 2026-08-31

Initial version!
