#!/usr/bin/env python3
"""Unit tests for weather alert zones, areas and event filtering."""

import configparser
from unittest.mock import AsyncMock, Mock

import pytest

from modules.service_plugins.weather_service import WeatherService

_ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:cap="urn:oasis:names:tc:emergency:cap:1.2">
  <entry>
    <id>urn:oid:2.49.test.tornado</id>
    <title>Tornado Warning issued June 27 at 10:00AM CDT until June 27 at 10:45AM CDT by NWS Austin/San Antonio TX</title>
    <updated>{now}</updated>
    <cap:event>Tornado Warning</cap:event>
  </entry>
  <entry>
    <id>urn:oid:2.49.test.fog</id>
    <title>Dense Fog Advisory issued June 27 at 10:00AM CDT until June 27 at 11:00AM CDT by NWS Austin/San Antonio TX</title>
    <updated>{now}</updated>
    <cap:event>Dense Fog Advisory</cap:event>
  </entry>
</feed>"""


def _service(mock_logger, **settings):
    config = configparser.ConfigParser()
    config.add_section("Weather")
    config.add_section("Weather_Service")
    config.set("Weather_Service", "my_position_lat", "29.4241")
    config.set("Weather_Service", "my_position_lon", "-98.4936")
    for key, value in settings.items():
        config.set("Weather_Service", key, value)
    bot = Mock()
    bot.logger = mock_logger
    bot.config = config
    bot.db_manager = Mock()
    bot.command_manager = Mock()
    bot.command_manager.send_channel_message = AsyncMock()
    return WeatherService(bot)


def _feed_response():
    from datetime import datetime, timezone

    response = Mock()
    response.ok = True
    response.status_code = 200
    response.text = _ATOM.format(now=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    return response


def test_url_defaults_to_bot_position(mock_logger):
    service = _service(mock_logger)
    assert service._alerts_url() == "https://api.weather.gov/alerts/active.atom?point=29.4241,-98.4936"


def test_url_uses_zones_over_area(mock_logger):
    service = _service(mock_logger, alerts_zones=" txc029, TXZ205 ,", alerts_area="TX")
    assert service._alerts_url() == "https://api.weather.gov/alerts/active.atom?zone=TXC029,TXZ205"


def test_url_uses_area_when_no_zones(mock_logger):
    service = _service(mock_logger, alerts_area="tx, ok")
    assert service._alerts_url() == "https://api.weather.gov/alerts/active.atom?area=TX,OK"


def test_malformed_zones_are_dropped_with_warning(mock_logger):
    service = _service(mock_logger, alerts_zones="TXC029, Bexar, TXZ20")
    assert service.alerts_zones == ["TXC029"]
    assert mock_logger.warning.call_count == 2


@pytest.mark.asyncio
async def test_zone_feed_skips_point_no_coverage_latch(mock_logger):
    service = _service(mock_logger, alerts_zones="TXC029")
    service.api_session = Mock()
    service.api_session.get = Mock(return_value=Mock(ok=False, status_code=404, text=""))

    await service._check_weather_alerts()
    await service._check_weather_alerts()

    assert service.api_session.get.call_count == 2
    assert service._nws_alerts_available is not False


@pytest.mark.asyncio
async def test_alerts_events_filters_by_cap_event(mock_logger):
    service = _service(mock_logger, alerts_zones="TXC029", alerts_events="tornado warning, Flood Watch")
    service.api_session = Mock()
    service.api_session.get = Mock(return_value=_feed_response())
    service._shorten_url = AsyncMock(return_value="")

    await service._check_weather_alerts()

    sent = service.bot.command_manager.send_channel_message.await_args_list
    assert len(sent) == 1
    assert "Tornado" in sent[0].args[1]
    # The skipped alert is remembered, so it is not re-evaluated on every poll.
    assert "urn:oid:2.49.test.fog" in service.seen_alert_ids


@pytest.mark.asyncio
async def test_blank_alerts_events_posts_everything(mock_logger):
    service = _service(mock_logger, alerts_zones="TXC029")
    service.api_session = Mock()
    service.api_session.get = Mock(return_value=_feed_response())
    service._shorten_url = AsyncMock(return_value="")

    await service._check_weather_alerts()

    assert service.bot.command_manager.send_channel_message.await_count == 2
