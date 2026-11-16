# Setz on OpenJarvis (laptop and phone)

[OpenJarvis](https://github.com/open-jarvis/OpenJarvis) is an open-source personal AI from Stanford that runs on
your own laptop (Apache 2.0). These files install it on Windows and set it up as Setz.

## What you get
- **Setz in OpenJarvis:** the Setz instructions plus the eight Setz skills: council, decision, radar, morning brief,
  evening review, weekly intelligence, inbox and accountability.
- **A choice of model:** Claude through an Anthropic API key (billed separately from your Claude plan), or a free
  local model that works offline and is less capable.
- **A private login:** one long access key protects every request for data and actions. Without it the app
  opens but shows nothing. The server listens only on the laptop.
- **Phone access:** Tailscale joins the laptop and the S26 Ultra into a private network. Nothing is opened to
  the internet. On the phone the app installs to the home screen.
- **Daily update at 06:30:** updates OpenJarvis to its latest release, its skill sources and Setz from this
  repository, rebuilds the app and restarts it. Log: `%LOCALAPPDATA%\OpenJarvis\setz\update.log`.

## Install (about 15 minutes)
1. Install Git for Windows if you do not have it: `winget install Git.Git`.
2. Clone this repository: `git clone https://github.com/sojitra12024-ui/Settleezy.git`. With a clone instead of a
   zip download, the daily update can pull Setz improvements.
3. In PowerShell:
   ```powershell
   cd Settleezy
   powershell -ExecutionPolicy Bypass -File setz\openjarvis\install-setz-jarvis.ps1
   ```
   For the free local model instead of Claude, add `-Brain local`.
4. When asked, paste your Anthropic API key. It is stored only in your Windows user profile.
5. Save the **access key** the script shows (it is also copied to the clipboard) in your password manager.
6. Open **Setz Jarvis** on the desktop. In Settings, paste the access key. On the first screen choose
   **Continue without sharing**: the other buttons send usage data and your email to the OpenJarvis project.

## Phone (Samsung S26 Ultra)
1. Install **Tailscale** from the Play Store and sign in with the same account as on the laptop.
2. Open the `https://….ts.net` address the install script printed.
3. In Settings, paste the access key. Then use the browser menu: **Add to Home screen**.

The laptop must be switched on and online. Tailscale is free for personal use.

## Limits
- **No live business data:** OpenJarvis does not use your Claude connectors. It cannot see your Outlook,
  calendar, OneDrive knowledge base or partner records. For live data, use the SETZ Command Centre or a Setz
  session in Claude, which also work on the phone in the Claude app.
- **Outlook:** OpenJarvis's Outlook connector logs in over IMAP with an app password. Microsoft 365 business
  mailboxes normally block that, so expect it not to work for `@settleezy.de`.
- **Self-improvement:** OpenJarvis can optimise skills from its own usage history (`jarvis optimize skills`).
  It needs weeks of use first and is a research feature. The daily update brings new versions; it does not make
  the agents learn on their own.
- **Not tested on Windows yet:** the scripts were checked for syntax only. Everything else was tested in a Linux
  container on 16 Nov 2026: install, access-key login, Setz skills loading, web app on desktop and phone sizes.

## Undo
```powershell
powershell -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\OpenJarvis\src\deploy\windows\jarvis-service.ps1" uninstall
Unregister-ScheduledTask -TaskName 'Setz Jarvis daily update' -Confirm:$false
tailscale serve reset
```
Then delete `%LOCALAPPDATA%\OpenJarvis` and `%USERPROFILE%\.openjarvis`.
