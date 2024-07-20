# Telegram Delay Channel Cloner

Telegram Bot which copies messages from a channel and pastes it into another after a specified amount of time

## Features
- Deleted messages do not get copied
- Batch processing to handle bursts of messages
- Deletion of old messages after a specified set of time
- Send updated edited message if message hasn't been sent to the target channel
- Configurable parameters via environment

Pending features:

- Ability to handle edited messages after being sent in target channel


## Parameters

| Variable Name      | Default Setting              | Required |
|--------------------|------------------------------|----------|
| `DB_LOCATION`      | `/data/messages.db`          | No       |
| `SOURCE_CHANNEL`   |                              | Yes      |
| `TARGET_CHANNEL`   |                              | Yes      |
| `DELAY`            |                              | Yes      |
| `POLLING`          | 5                            | No       |
| `BOT_TOKEN`        |                              | Yes      |
| `COPY_MESSAGE`     | True                         | No       |
| `RETENTION_PERIOD` | 7                            | No       |
| `BATCH_SIZE`       | 10                           | No       |

## Run

You will need to run `docker-compose.yml`

## Maintainers

[@GeiserX](https://github.com/GeiserX)

## Contributing

Feel free to dive in! [Open an issue](https://github.com/GeiserX/telegram-delay-channel-cloner/issues/new) or submit PRs

JW Library Plus follows the [Contributor Covenant](http://contributor-covenant.org/version/2/1/) Code of Conduct

### Contributors

This project exists thanks to all the people who contribute
<a href="https://github.com/GeiserX/telegram-delay-channel-cloner/graphs/contributors"><img src="https://opencollective.com/telegram-delay-channel-cloner/contributors.svg?width=890&button=false" /></a>
