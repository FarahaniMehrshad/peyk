# Peyk

A vim-style Telegram client for the Windows terminal. Python and Textual, no WSL, no compiler.

Work in progress: everything below works; installing it as a command comes next.

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
| `+` | react to the selected message, or take it back |
| `o` `p` `a` | open the file or link, show the picture, send a file |
| `s` `g` | send a sticker, a GIF |
| `/` `?` `@` | find chats, messages, people in this chat and what they wrote |
| `:` `F1` | command bar, all keys |
| `L` `q` | log out, quit |

They work on any keyboard layout: Peyk looks at the physical key.

Change them in `config.toml`, as printed on a US keyboard:

```toml
[keys]
react = "-"
find_chat = "ctrl+f"
logout = ""          # no key
```

The actions: `down` `up` `page_down` `page_up` `newest` `focus_list` `focus_chat` `open` `next_tab`
`previous_tab` `close_tab` `close_others` `fullscreen` `deselect` `write` `reply` `edit` `delete`
`copy` `react` `open_media` `preview` `attach` `stickers` `gifs` `find_chat` `find_message`
`find_sender` `command` `help` `quit` `logout`. If two have the same key, or one is not a key, Peyk says so and does not
start. Arrows, PgUp, PgDn, End and `Ctrl+Q` always work; digits are for folders and tabs.

A chat is marked read only while it is open, the terminal has focus, and the message is on screen.

## Writing

`Enter` sends, `Ctrl+Enter` or `Ctrl+J` starts a new line, `Esc` stops writing and keeps the text.
Every chat has its own draft until you quit. `**bold**`, `__italic__`, `~~strike~~`, `` `code` ``,
```` ```block``` ````, `||spoiler||` and `[text](https://...)` become formatting; set
`formatting = false` under `[text]` to send them as typed.

A message that cannot be sent stays in the chat, marked `!`. After network trouble Peyk tries again
by itself, never sending twice. If Telegram refuses it, select it: `Enter` tries again, `e` takes it
back for editing, `d` discards it. Quitting warns you; unsent messages and drafts are not kept.

## Reactions and typing

`+` on a selected message shows the reactions that this chat allows, your favourites first: `1`-`9`
gives one of the first nine, or move and press `Enter`. Choosing yours again takes it back. Yours are
highlighted under the message. Custom emoji are shown as the ordinary emoji they stand for.

Who is typing shows in the list and above the compose box. Others see when you type, unless you set
`typing = false` under `[privacy]` in `config.toml`.

## Notifications

New messages are announced by Windows, one notification per chat, except in muted chats and in the
chat you are looking at. A click opens the chat. They disappear when you read the chat anywhere, when
the message is deleted, and when Peyk ends. Under `[notifications]`, `show_content = false` hides what
was written and `enabled = false` turns them off. Windows only shows them from senders it knows, so
Peyk registers its name for your account (`HKCU\Software\Classes\AppUserModelId\Peyk.Telegram`);
logging out removes that.

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

`s` shows your stickers: recent ones, favourites, then your sets, in the order you gave them; `Tab`
goes to the next pack. `g` shows your saved GIFs. Move with `h` `j` `k` `l` and send with `Enter`; if
you were replying, the sticker is the reply. Animated stickers and GIFs show as a still picture.
Packs and GIFs are added in an official app.

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
