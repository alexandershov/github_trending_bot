import datetime as dt

import pytest
import responses

from github_trending_bot import bot


def test_get_config():
    environment = {
        'GITHUB_TOKEN': 'some_github_token',
        'TELEGRAM_TOKEN': 'some_telegram_token',
    }
    config = bot.get_config(environment)
    assert config.github_token == 'some_github_token'
    assert config.telegram_token == 'some_telegram_token'


@pytest.mark.parametrize('environment', [
    # no GITHUB_TOKEN
    {'TELEGRAM_TOKEN': 'some_telegram_token'},
    # no TELEGRAM_TOKEN
    {'GITHUB_TOKEN': 'some_github_token'},
])
def test_get_config_failure(environment):
    with pytest.raises(bot.InvalidConfig):
        bot.get_config(environment)


def _make_repo_item(name, description, html_url):
    return {
        'name': name,
        'description': description,
        'html_url': html_url,
    }


@responses.activate
def test_github_api():
    responses.add(
        responses.GET,
        'https://api.github.com/search/repositories',
        json={
            'items': [
                _make_repo_item('some_name', 'some_description', 'http://example.com'),
            ]
        }
    )
    api = bot.GithubApi('some_github_token')
    repositories = api.find_trending_repositories(
        created_after=dt.datetime(2017, 1, 5, 12, 3, 23, 686),
        limit=1)
    assert len(responses.calls) == 1
    call = responses.calls[0]
    assert call.request.path_url == 'https://api.github.com/search/repositories'
