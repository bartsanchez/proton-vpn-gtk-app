from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock, patch
import time

import pytest

from proton.vpn.core_api.client_config import ClientConfig, DEFAULT_CLIENT_CONFIG
from proton.vpn.servers.list import ServerList

from proton.vpn.app.gtk.services import VPNDataRefresher

from tests.unit.utils import process_gtk_events


@pytest.fixture
def thread_pool_executor():
    with ThreadPoolExecutor() as thread_pool_executor:
        yield thread_pool_executor


@pytest.fixture
def expected_client_config():
    return ClientConfig.from_dict(DEFAULT_CLIENT_CONFIG)


@pytest.fixture
def expected_server_list():
    return ServerList(apidata={
        "LogicalServers": [
            {
                "ID": 1,
                "Name": "AR#1",
                "Status": 1,
                "Servers": [{"Status": 1}],
                "ExitCountry": "AR",
                "Tier": 0,
            }
        ],
        "LogicalsUpdateTimestamp": time.time(),
        "LoadsUpdateTimestamp": time.time()
    })


def test_notify_subscribers_when_client_config_is_updated(
        thread_pool_executor, expected_client_config
):
    mock_api = Mock()
    mock_api.get_client_config.return_value = expected_client_config

    vpn_data_refresher = VPNDataRefresher(thread_pool_executor, mock_api)

    # Connect new-client-config callback
    new_client_config_event = Event()
    client_config_received = None
    def new_client_config_callback(_vpn_data_refresher, client_config):
        nonlocal client_config_received
        client_config_received = client_config
        new_client_config_event.set()
    vpn_data_refresher.connect("new-client-config", new_client_config_callback)

    # Connect vpn-data-ready callback
    vpn_data_ready_event = Event()
    vpn_data_refresher.connect("vpn-data-ready", lambda *_: vpn_data_ready_event.set())

    vpn_data_refresher.retrieve_client_config()

    process_gtk_events()

    assert new_client_config_event.wait(timeout=0)
    assert client_config_received is expected_client_config
    assert not vpn_data_ready_event.wait(timeout=0)


def test_notify_subscribers_when_server_list_is_updated(
        thread_pool_executor, expected_server_list
):
    mock_api = Mock()
    mock_api.servers.get_server_list.return_value = expected_server_list

    vpn_data_refresher = VPNDataRefresher(thread_pool_executor, mock_api)

    # Connect new-server-list callback
    new_server_list_event = Event()
    server_list_received = None
    def new_server_list_callback(_vpn_data_refresher, client_config):
        nonlocal server_list_received
        server_list_received = client_config
        new_server_list_event.set()
    vpn_data_refresher.connect("new-server-list", new_server_list_callback)

    # Connect vpn-data-ready callback
    vpn_data_ready_event = Event()
    vpn_data_refresher.connect("vpn-data-ready", lambda *_: vpn_data_ready_event.set())

    vpn_data_refresher.retrieve_server_list()

    process_gtk_events()

    assert new_server_list_event.wait(timeout=0)
    assert server_list_received is expected_server_list
    assert not vpn_data_ready_event.wait(timeout=0)


def test_enable_notifies_subscribers_when_vpn_data_is_ready(
        thread_pool_executor, expected_client_config, expected_server_list
):
    mock_api = Mock()
    mock_api.get_client_config.return_value = expected_client_config
    mock_api.servers.get_server_list.return_value = expected_server_list

    vpn_data_refresher = VPNDataRefresher(thread_pool_executor, mock_api)

    # Connect vpn-data-ready callback
    received_server_list = None
    received_client_config = None
    vpn_data_ready_event = Event()
    def vpn_data_ready_callback(_vpn_data_refresher, server_list, client_config):
        nonlocal received_server_list, received_client_config
        received_server_list = server_list
        received_client_config = client_config
        vpn_data_ready_event.set()
    vpn_data_refresher.connect("vpn-data-ready", vpn_data_ready_callback)

    vpn_data_refresher.enable()

    process_gtk_events()

    assert vpn_data_ready_event.wait(timeout=0)
    assert received_server_list is expected_server_list
    assert received_client_config is expected_client_config


@patch("proton.vpn.app.gtk.services.vpn_data_refresher.GLib.timeout_add")
@patch("proton.vpn.app.gtk.services.vpn_data_refresher.GLib.source_remove")
def test_disable_resets_state(
        patched_glib_source_remove, patched_glib_timeout_add,
        thread_pool_executor, expected_client_config, expected_server_list
):
    mock_api = Mock()
    mock_api.get_client_config.return_value = expected_client_config
    mock_api.servers.get_server_list.return_value = expected_server_list

    timeout_add_source_ids = [1, 2]
    patched_glib_timeout_add.side_effect = timeout_add_source_ids

    vpn_data_refresher = VPNDataRefresher(thread_pool_executor, mock_api)

    vpn_data_refresher.enable()

    process_gtk_events()

    assert vpn_data_refresher.is_vpn_data_ready

    vpn_data_refresher.disable()

    assert not vpn_data_refresher.is_vpn_data_ready
    # Assert that GLib sources added to periodically update VPN data have been removed.
    assert len(patched_glib_source_remove.mock_calls) == 2
    for nth_call, call in enumerate(patched_glib_source_remove.mock_calls):
        assert call.args[0] == timeout_add_source_ids[nth_call]
