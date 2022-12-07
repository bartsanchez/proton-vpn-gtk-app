"""
Certain VPN data like the server list or the client configuration needs to
refreshed periodically to keep it up to date.

This module defines the required services to do so.
"""
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass

from gi.repository import GLib, GObject

from proton.vpn.servers.list import ServerList
from proton.vpn.core_api.api import ProtonVPNAPI
from proton.vpn.core_api.client_config import ClientConfig
from proton.vpn import logging


logger = logging.getLogger(__name__)

# Number of seconds to wait before checking if the servers cache expired.
RELOAD_INTERVAL_IN_SECONDS = 60


@dataclass
class VPNDataRefresherState:
    """
    Contextual data that is kept about the current user session. All this data
    is reset after a logout/login.
    """
    api_data_retrieval_complete = False
    reload_servers_source_id: int = None
    reload_client_config_source_id: int = None
    client_config: ClientConfig = None
    server_list: ServerList = None
    last_server_list_update_time: int = 0


class VPNDataRefresher(GObject.Object):
    """
    Service in charge of:
        - retrieving the required VPN data from Proton's REST API
          to be able to establish VPN connection,
        - keeping it up to date and
        - notifying subscribers when VPN data has been updated.

    Attributes:
        server_list: List of VPN servers to be presented to the user.
        client_config: VPN client configuration to be able to establish a VPN
        connection to any of the available servers.
    """
    def __init__(
        self,
        thread_pool_executor: ThreadPoolExecutor,
        proton_vpn_api: ProtonVPNAPI
    ):
        super().__init__()
        self._thread_pool = thread_pool_executor
        self._api = proton_vpn_api
        self._state = VPNDataRefresherState()

    @property
    def server_list(self) -> ServerList:
        """
        Returns the list of available VPN servers.
        """
        return self._state.server_list

    @server_list.setter
    def server_list(self, server_list: ServerList):
        """Sets the list of available VPN servers."""
        self._state.server_list = server_list

    @property
    def client_config(self) -> ClientConfig:
        """Returns the VPN client configuration."""
        return self._state.client_config

    @client_config.setter
    def client_config(self, client_config: ClientConfig):
        """Sets the VPN client configuration."""
        self._state.client_config = client_config

    @GObject.Signal(name="new-server-list", arg_types=(object,))
    def new_server_list(self, server_list: ServerList):
        """Signal emitted when the VPN server list has been updated."""

    @GObject.Signal(name="new-client-config", arg_types=(object,))
    def new_client_config(self, client_config: ClientConfig):
        """Signal emitted when the VPN client configuration has been updated."""

    @GObject.Signal(name="vpn-data-ready", arg_types=(object, object))
    def vpn_data_ready(self, server_list: ServerList, client_config: ClientConfig):
        """Signal emitted when the VPN client configuration has been updated."""

    @property
    def is_vpn_data_ready(self) -> bool:
        """Returns whether the necessary data from API has already been retrieved or not."""
        return bool(self.server_list and self.client_config)

    def enable(self):
        """Start retrieving data periodically from Proton's REST API."""
        self.retrieve_client_config()
        self.retrieve_server_list()
        self._state.reload_client_config_source_id = GLib.timeout_add(
            interval=RELOAD_INTERVAL_IN_SECONDS * 1000,
            function=self.retrieve_client_config
        )
        self._state.reload_servers_source_id = GLib.timeout_add(
            interval=RELOAD_INTERVAL_IN_SECONDS * 1000,
            function=self.retrieve_server_list
        )
        logger.info(
            "VPN data refresher service enabled.",
            category="APP", subcategory="VPN_DATA_REFRESHER", event="ENABLE"
        )

    def disable(self):
        """Stops retrieving data periodically from Proton's REST API."""
        if self._state.reload_client_config_source_id is not None:
            GLib.source_remove(self._state.reload_client_config_source_id)
        if self._state.reload_servers_source_id is not None:
            GLib.source_remove(self._state.reload_servers_source_id)

        self._state = VPNDataRefresherState()
        logger.info(
            "VPN data refresher service disabled.",
            category="APP", subcategory="VPN_DATA_REFRESHER", event="DISABLE"
        )

    def retrieve_client_config(self) -> Future:
        """Returns client config."""
        logger.debug(
            "Retrieving client configuration...",
            category="API", subcategory="CLIENT_CONFIG", event="GET"
        )
        future = self._thread_pool.submit(
            self._api.get_client_config
        )
        future.add_done_callback(
            lambda f: GLib.idle_add(
                self._on_client_config_retrieved, f
            )
        )
        return future

    def retrieve_server_list(self) -> Future:
        """
        Requests the list of servers. Note that a remote API call is only
        triggered if the server list cache expired.
        :return: A future wrapping the server list.
        """
        logger.debug(
            "Retrieving server list...",
            category="API", subcategory="LOGICALS", event="GET"
        )
        future = self._thread_pool.submit(
            self._api.servers.get_server_list,
            force_refresh=False
        )
        future.add_done_callback(
            lambda future: GLib.idle_add(self._on_servers_retrieved, future)
        )
        return future

    def _on_client_config_retrieved(self, future_client_config: Future):
        new_client_config = future_client_config.result()
        if new_client_config is not self.client_config:
            self.client_config = new_client_config
            self.emit("new-client-config", self.client_config)
            self._emit_signal_once_all_required_vpn_data_is_available()
        else:
            logger.debug(
                "Skipping client configuration reload because it's already up "
                "to date.", category="APP", subcategory="CLIENT_CONFIG",
                event="reload"
            )

    def _on_servers_retrieved(self, future_server_list: Future):
        server_list = future_server_list.result()
        if self._is_server_list_outdated(server_list):
            self.server_list = server_list
            self._state.last_server_list_update_time = server_list.loads_update_timestamp
            self.emit("new-server-list", server_list)
            self._emit_signal_once_all_required_vpn_data_is_available()
        else:
            logger.debug(
                "Skipping server list reload because it's already up to date.",
                category="APP", subcategory="SERVERS", event="RELOAD"
            )

    def _emit_signal_once_all_required_vpn_data_is_available(self):
        if not self._state.api_data_retrieval_complete and self.is_vpn_data_ready:
            self.emit("vpn-data-ready", self.server_list, self.client_config)
            self._state.api_data_retrieval_complete = True

    def _is_server_list_outdated(self, new_server_list: ServerList):
        """Returns if server list is outdated or not."""
        new_timestamp = new_server_list.loads_update_timestamp
        return self._state.last_server_list_update_time < new_timestamp
