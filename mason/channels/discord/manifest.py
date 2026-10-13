"""Discord management contract."""

from mason.channels._manifest import DIRECT_GROUP_POLICIES, field, required
from mason.channels.contracts import ChannelSetupSpec
from mason.channels.discord.validation import validate
from mason.channels.plugin import ChannelPlugin

SETUP_SPEC = ChannelSetupSpec(
    fields={
        "token": field("secret"),
        "proxy": field(),
        "proxyUsername": field(),
        "proxyPassword": field("secret"),
        "allowFrom": field("list", snapshot=False),
        "allowChannels": field("list"),
        "groupPolicy": field("enum", choices=DIRECT_GROUP_POLICIES, default="mention"),
        "intents": field("int", default=37377),
        "readReceiptEmoji": field(default="👀"),
        "workingEmoji": field(default="🔧"),
        "workingEmojiDelay": field("float", default=2.0),
        "streaming": field("bool", default=True),
        "replyToMessage": field("bool", default=False),
    },
    required=(required("token"),),
    official_url="https://discord.com/developers/applications",
    validator=validate,
    verifies_connection=True,
)

PLUGIN = ChannelPlugin(
    name="discord",
    display_name="Discord",
    runtime=f"{__package__}.runtime:DiscordChannel",
    setup=SETUP_SPEC,
    dependencies=("discord.py>=2.5.2,<3.0.0",),
)
