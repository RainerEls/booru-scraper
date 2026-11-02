# Notifications

Notifications are optional and off until you add a service. In Settings, set **Apprise URLs** to one or more [Apprise URLs](https://appriseit.com/services/), separated by commas, such as `ntfys://my-topic,discord://webhook_id/webhook_token`. An existing [Apprise API](https://github.com/caronc/apprise-api) server works too, through `apprise://host/key`.

To send only some notifications, list them in **Events to send**, separated by commas. Leave it empty for all of them.

| Event | Sent when |
| --- | --- |
| `scrape_finished` | A scrape you started finishes without problems |
| `schedule_finished` | A scheduled scrape uploads new posts. Runs that find nothing new stay silent |
| `scrape_failed` | Any scrape hits an error or fails to upload a post |
| `scrape_interrupted` | A restart cuts a scrape short |
| `integrity_problems` | An integrity check finds missing or damaged posts, or can't finish |

Stopping a scrape yourself sends nothing. A failed notification is logged and never affects the scrape.
