"""Desktop channel management contract."""

from mason.channels._manifest import field
from mason.channels.contracts import ChannelSetupSpec
from mason.channels.plugin import ChannelPlugin

SETUP_SPEC = ChannelSetupSpec(
    fields={
        "host": field(default="127.0.0.1"),
        "port": field("int", default=8765),
        "allowFrom": field("list"),
        "streaming": field("bool", default=True),
        "showReasoning": field("bool", default=True),
        "sendProgress": field("bool", default=True),
        "sendToolHints": field("bool", default=True),
        "trustLocalhost": field("bool", default=False),
        "maxUploadMb": field("int", default=25),
        "maxFetchMb": field("int", default=25),
    },
)

PLUGIN = ChannelPlugin(
    name="desktop",
    display_name="MasonDesk",
    runtime=f"{__package__}.runtime:DesktopChannel",
    setup=SETUP_SPEC,
    dependencies=(),
)
