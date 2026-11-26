# Privacy

AIMarkLens is a Windows desktop application. Image and text analysis is performed locally unless you choose an
online feature. Original files are opened read-only; reports and copies are saved separately.

## Network use

- Online verification sends an image to the verification service or services you select. By default, AIMarkLens
  asks before uploading. If you enable automatic online verification, it may upload after analysis without asking
  each time.
- When network access is enabled, AIMarkLens can fetch remote Content Credentials, the C2PA trust list, and
  external tools. These operations download data; they do not upload the image for AI detection.
- The selected online service receives the image and request data needed to perform its check. Its own privacy
  policy and retention practices apply. Do not submit images you are not allowed to share.
- Network requests necessarily expose basic connection information, such as your IP address, to the services
  contacted.

## Data stored on your computer

- **Settings and service credentials:** stored in the user's application settings. On Windows, saved API secrets
  are protected with Windows DPAPI for the current Windows user. Google authentication may also use credentials
  managed by Google Cloud tools or a service-account file that you selected.
- **Analysis history:** when enabled, AIMarkLens stores image hashes, perceptual hashes, file names, findings,
  timestamps, copy relationships, and online verification results. It does not store image contents in the history.
  You can turn history off or clear it in the application.
- **Reports and exported copies:** saved to the output folder configured in Settings.
- **Downloaded tools:** stored locally for use by AIMarkLens.

The application does not include usage analytics or telemetry collection.

## Managing and removing data

Use the application settings to clear analysis history and remove saved service credentials. During uninstall, you
can choose whether to remove AIMarkLens settings, API keys, history, and downloaded tools. Reports and copies in
your output folder are separate and may need to be removed manually.

Data already sent to an online verification provider is subject to that provider's policies; deleting local
AIMarkLens data does not delete data held by a provider.

For security vulnerability reports, see [SECURITY.md](SECURITY.md). For privacy questions or documentation
corrections, open an issue without including private files or credentials.

