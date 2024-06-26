import os
import re
from pathlib import Path

from mkdocs.config import config_options
from mkdocs.config.base import Config as MkDocsBaseConfig
from mkdocs.config.defaults import MkDocsConfig
from mkdocs.exceptions import PluginError
from mkdocs.plugins import BasePlugin as MkDocsBasePlugin
from mkdocs.plugins import get_plugin_logger
from mkdocs.structure.files import Files as MkDocsFiles
from mkdocs.structure.pages import Page as MkDocsPage

from kroki.client import KrokiClient, KrokiResponse
from kroki.config import KrokiDiagramTypes

log = get_plugin_logger(__name__)


class DeprecatedDownloadImagesCompat(config_options.Deprecated):
    def pre_validation(self, config: "KrokiPluginConfig", key_name: str) -> None:
        """Set `HttpMethod: 'POST'`, if enabled"""
        if config.get(key_name) is None:
            return

        self.warnings.append(self.message.format(key_name))

        download_images: bool = config.pop(key_name)
        if download_images:
            config.HttpMethod = "POST"


class KrokiPluginConfig(MkDocsBaseConfig):
    ServerURL = config_options.URL(default=os.getenv("KROKI_SERVER_URL", "https://kroki.io"))
    EnableBlockDiag = config_options.Type(bool, default=True)
    Enablebpmn = config_options.Type(bool, default=True)
    EnableExcalidraw = config_options.Type(bool, default=True)
    EnableMermaid = config_options.Type(bool, default=True)
    EnableDiagramsnet = config_options.Type(bool, default=False)
    HttpMethod = config_options.Choice(choices=["GET", "POST"], default="GET")
    UserAgent = config_options.Type(str, default=f"{__name__}/0.7.1")
    FencePrefix = config_options.Type(str, default="kroki-")
    FileTypes = config_options.Type(list, default=["svg"])
    FileTypeOverrides = config_options.Type(dict, default={})
    FailFast = config_options.Type(bool, default=False)

    DownloadImages = DeprecatedDownloadImagesCompat(moved_to="HttpMethod: 'POST'")
    DownloadDir = config_options.Deprecated(removed=True)


class KrokiPlugin(MkDocsBasePlugin[KrokiPluginConfig]):
    diagram_types: KrokiDiagramTypes
    kroki_client: KrokiClient
    from_file_prefix = "@from_file:"
    global_config: MkDocsConfig
    fail_fast: bool

    def on_config(self, config: MkDocsConfig) -> MkDocsConfig:
        log.debug("Configuring", extra={"config": self.config})

        self.diagram_types = KrokiDiagramTypes(
            blockdiag_enabled=self.config.EnableBlockDiag,
            bpmn_enabled=self.config.Enablebpmn,
            excalidraw_enabled=self.config.EnableExcalidraw,
            mermaid_enabled=self.config.EnableMermaid,
            diagramsnet_enabled=self.config.EnableDiagramsnet,
            file_types=self.config.FileTypes,
            file_type_overrides=self.config.FileTypeOverrides,
        )

        self.kroki_client = KrokiClient(
            server_url=self.config.ServerURL,
            http_method=self.config.HttpMethod,
            user_agent=self.config.UserAgent,
            diagram_types=self.diagram_types,
            fail_fast=self.config.FailFast,
        )

        self.global_config = config

        self.fail_fast = self.config.FailFast

        return config

    def _replace_kroki_block(self, match_obj: re.Match, files: MkDocsFiles, page: MkDocsPage) -> str:
        kroki_type = match_obj.group(1).lower()
        kroki_options = match_obj.group(2)
        kroki_data = match_obj.group(3)

        if kroki_data.startswith(self.from_file_prefix):
            file_name = kroki_data.removeprefix(self.from_file_prefix).strip()
            file_path = Path(self.global_config.docs_dir) / file_name
            log.debug('Reading kroki block from file: "%s"', file_path.absolute())
            try:
                with open(file_path) as data_file:
                    kroki_data = data_file.read()
            except OSError as error:
                msg = f'Can\'t read file: "{file_path.absolute()}"'
                log.exception(msg)
                if self.fail_fast:
                    raise PluginError(msg) from error

                return f'!!! error "{msg}"'

        kroki_diagram_options = dict(x.split("=") for x in kroki_options.strip().split(" ")) if kroki_options else {}

        response: KrokiResponse = self.kroki_client.get_image_url(
            kroki_type, kroki_data, kroki_diagram_options, files, page
        )
        log.debug("%s", response)
        if response.is_ok():
            return f"![Kroki]({response.image_url})"

        if self.fail_fast:
            raise PluginError(response.err_msg)

        return f'!!! error "{response.err_msg}"\n\n```\n{kroki_data}\n```'

    def on_page_markdown(self, markdown: str, files: MkDocsFiles, page: MkDocsPage, **_kwargs) -> str:
        log.debug("on_page_markdown [page: %s]", page)

        kroki_regex = self.diagram_types.get_block_regex(self.config.FencePrefix)
        pattern = re.compile(kroki_regex, flags=re.IGNORECASE + re.DOTALL)

        def replace_kroki_block(match_obj):
            return self._replace_kroki_block(match_obj, files, page)

        return re.sub(pattern, replace_kroki_block, markdown)
