import pytest

from github_trending_bot import bot


def test_get_config():
    environment = {
        'GITHUB_TOKEN': 'some_github_token',
        'TELEGRAM_TOKEN': 'some_telegram_token',
    }
    config = bot.get_config(environment)
    assert config.github_token == 'some_github_token'
    assert config.telegram_token == 'some_telegram_token'


def test_get_config_no_github_token():
    environment = {
        'TELEGRAM_TOKEN': 'some_telegram_token',
    }
    with pytest.raises(bot.InvalidConfig):
        bot.get_config(environment)


def test_get_config_no_telegram_token():
    environment = {
        'GITHUB_TOKEN': 'some_github_token',
    }
    with pytest.raises(bot.InvalidConfig):
        bot.get_config(environment)
