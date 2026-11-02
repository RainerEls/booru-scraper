# Login and security

Login is optional and off by default, so anyone who can reach the port can use the app until you turn it on. The Settings page stays read-only until you choose one of these:

| Your setup | What to set |
| --- | --- |
| The app has its own password | `APP_PASSWORD` |
| A login proxy is in front, with no directly published port | `ALLOW_SETTINGS_WITHOUT_LOGIN=true` |
| A network where you trust everyone | `ALLOW_SETTINGS_WITHOUT_LOGIN=true` |

`ALLOW_SETTINGS_WITHOUT_LOGIN` doesn't detect a proxy. It just lets anyone who reaches the app change its settings, including where it sends your Szurubooru token, so make sure nothing can bypass your proxy.

Set both in `.env` and restart. If the password contains a `$`, put it in single quotes.

## Exposing the app

The password login is meant for a trusted network. Over plain HTTP, someone able to watch the traffic can read the password, and there's no MFA. Anything reachable from the internet should sit behind HTTPS and a proper auth proxy.

- Once the app is served over HTTPS, set `COOKIE_SECURE=true`. Leave it off for plain HTTP, or browsers will refuse the login cookie.
- A proxy has to pass the original `Host` header, or requests that change anything are refused as cross-site.

## Password login details

- Sessions last a week and live in memory, so a restart logs everyone out. Changing the password needs a restart, so it does too.
- Five wrong passwords lock that address out of new logins for five minutes. Behind a proxy, every client can look like the same address.
- `/health` stays open so monitoring keeps working. It only reports whether the app can reach its database.
