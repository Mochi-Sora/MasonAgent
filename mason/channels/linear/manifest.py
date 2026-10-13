"""Dependency-free management contract for the native Linear Agent channel."""

from mason.channels._manifest import field, required_fields
from mason.channels.contracts import ChannelManagementSpec, ChannelSetupSpec
from mason.channels.linear.state import local_state_present
from mason.channels.linear.validation import validate
from mason.channels.plugin import ChannelPlugin

SETUP_SPEC = ChannelSetupSpec(
    official_url="https://linear.app/settings/api/applications/new",
    fields={
        "clientId": field(),
        "clientSecret": field("secret"),
        "webhookSigningSecret": field("secret"),
        "publicBaseUrl": field(),
        "host": field(default="0.0.0.0"),
        "port": field("int", default=3979),
        "webhookPath": field(default="/linear/webhook"),
        "oauthCallbackPath": field(default="/linear/oauth/callback"),
        "allowFrom": field("list"),
        "showReasoning": field("bool", default=True),
    },
    required=required_fields(
        "clientId",
        "clientSecret",
        "webhookSigningSecret",
        "publicBaseUrl",
    ),
    validator=validate,
)

PLUGIN = ChannelPlugin(
    name="linear",
    display_name="Linear",
    runtime=f"{__package__}.runtime:LinearChannel",
    connector=f"{__package__}.connect:LinearConnectStore",
    setup=SETUP_SPEC,
    management=ChannelManagementSpec(local_state_present=local_state_present),
)
