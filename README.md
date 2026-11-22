# Peyk

A vim-style Telegram client for the Windows terminal. Python and Textual, no WSL, no compiler.

Work in progress: you can sign in, read, write, search, and send and open files. Reactions,
typing indicators and notifications come next.

## Run

Needs [uv](https://docs.astral.sh/uv/), Python 3.13 and Windows Terminal.

```powershell
uv run peyk                # first run asks for your API keys
uv run peyk --demo         # offline, made-up data
uv run peyk --demo=login   # offline, from the sign-in screen (code 12345, password demo)
```

API keys: <https://my.telegram.org>, under "API development tools".

## Keys

| Key | Action |
|---|---|
| `j` `k`, arrows | move in the list, or select a message |
| `J` `K`, PgDn PgUp | ten chats, or scroll ten lines |
| `G`, End | last chat, or newest message |
| `h` `l` | go to the list, or to the conversation |
| `Enter` | open the chat, or deselect the message |
| `0`-`9` | folder (in the list) or tab (in a conversation) |
| `Tab` `Shift+Tab` | next, previous tab |
| `x` `X` | close this tab, close the others |
| `f` | fullscreen |
| `i` `r` `e` | write, reply to the selected message, edit it (or your last one) |
| `d` `y` | delete (asks first), copy |
| `o` `p` `a` | open the file or link, show the picture, send a file |
| `/` `?` `@` | find chats, messages, people in this chat and what they wrote |
| `:` `F1` | command bar, all keys |
| `L` `q` | log out, quit |

They work on any keyboard layout: Peyk looks at the physical key.

A chat is marked read only while it is open, the terminal has focus, and the message is on screen.

## Writing

`Enter` sends, `Ctrl+Enter` or `Ctrl+J` starts a new line, `Esc` stops writing and keeps the text.
Every chat has its own draft until you quit. `**bold**`, `__italic__`, `~~strike~~`, `` `code` ``,
```` ```block``` ````, `||spoiler||` and `[text](https://...)` become formatting; set
`formatting = false` under `[text]` to send them as typed.

A message that cannot be sent stays in the chat, marked `!`. After network trouble Peyk tries again
by itself, never sending twice. If Telegram refuses it, select it: `Enter` tries again, `e` takes it
back for editing, `d` discards it. Quitting warns you; unsent messages and drafts are not kept.

## Searching

`?` searches this chat, or all chats from the list; `Tab` switches. `Enter` goes to the message, and
`G` returns to the newest. Persian and Arabic spellings of the same letter match when finding chats.

## Files and pictures

`o` downloads a file and opens it (`o` again stops the download), or opens a link. Files go to
`Downloads\Peyk` (`:downloads` shows it), are never replaced, and are marked as coming from the web.
Only types that show something are opened; programs, scripts and the like are shown in their folder.
A link whose text hides where it goes is shown first, and only `http` and `https` are opened.

`a` sends a file: type to filter, or paste a path. Pictures go as photos, without their metadata
(place, camera); `Tab` sends them as files, untouched. What you write next is the caption. `d` on the
message stops the upload.

Pictures show between the messages, sharp if the terminal supports Sixel (Windows Terminal 1.22),
otherwise as coloured blocks. `p` fills the window with one; `h` `l` walk through the others.
Spoilers stay covered until you select them. They are kept in memory only. In `config.toml`,
`[images] inline = false` leaves only `p`, and `mode = "off"` turns them off.

## Colours

Peyk takes the colours of your terminal, including a see-through background. They are read when it
starts, so restart it after changing the colour scheme. Other themes: `theme = "catppuccin-mocha"`
(or `nord`, `gruvbox`, `tokyo-night`, `dracula`, `solarized-light`, ...) under `[appearance]` in
`config.toml`.

## Persian, Arabic and Hebrew

Windows Terminal joins letters but does not order them, so Peyk does. Use a terminal font that has
the letters: Cascadia Mono does; many Nerd Fonts do not, and then Windows borrows one that does not
fit the grid. A list keeps your font and adds Persian, in the profile's settings:

```json
"font": { "face": "Hack Nerd Font, Cascadia Mono" }
```

## Where things are

| What | Where |
|---|---|
| Settings | `%APPDATA%\peyk\config.toml` |
| API keys and proxy, encrypted | `%LOCALAPPDATA%\peyk\secrets.bin` |
| Login, key encrypted | `%LOCALAPPDATA%\peyk\account.session` |
| Log, redacted | `%LOCALAPPDATA%\peyk\peyk.log` |

Encryption is Windows DPAPI: the files are useless to another Windows account or computer, but
not to other programs running as you. Names, usernames and phone numbers of your contacts are not
stored. Logging out revokes the session and deletes the login and the API keys.

## Proxy

Enter one on the setup screen (`Ctrl+S` while signing in): `socks5://`, `http://`,
`tg://proxy?...`, `tg://socks?...`, a `t.me` proxy link, or `system` for the Windows proxy.
FakeTLS MTProxies (secret starting with `ee`) are not supported yet.

## Develop

```powershell
uv run pytest
uv run ruff check
```

## Credits

Inspired by [telegramtui](https://github.com/k4dy/telegramtui). No code is shared.
