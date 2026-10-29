from dataclasses import dataclass, field
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    access_token: str = field(default="", repr=False)
    client_id: str = ""
    client_secret: str = field(default="", repr=False)
    redirect_uri: str = "http://127.0.0.1:8765/callback"
    token_file: Path = ROOT / ".tokens.json"
    expected_user_id: str = ""
    consumer_key: str = field(default="", repr=False)
    consumer_secret: str = field(default="", repr=False)
    oauth1_access_token: str = field(default="", repr=False)
    oauth1_access_secret: str = field(default="", repr=False)

    @property
    def has_oauth1(self) -> bool:
        return all((self.consumer_key, self.consumer_secret, self.oauth1_access_token, self.oauth1_access_secret))

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(ROOT / ".env", override=False)
        token_path = Path(os.environ.get("X_TOKEN_FILE", ".tokens.json")).expanduser()
        if not token_path.is_absolute():
            token_path = ROOT / token_path
        return cls(
            access_token=os.environ.get("X_USER_ACCESS_TOKEN", "").strip(),
            client_id=os.environ.get("X_CLIENT_ID", "").strip(),
            client_secret=os.environ.get("X_CLIENT_SECRET", "").strip(),
            redirect_uri=os.environ.get("X_REDIRECT_URI", cls.redirect_uri),
            token_file=token_path,
            expected_user_id=os.environ.get("X_EXPECTED_USER_ID", "").strip(),
            consumer_key=os.environ.get("X_CONSUMER_KEY", "").strip(),
            consumer_secret=os.environ.get("X_CONSUMER_SECRET", "").strip(),
            oauth1_access_token=os.environ.get("X_OAUTH1_ACCESS_TOKEN", "").strip(),
            oauth1_access_secret=os.environ.get("X_OAUTH1_ACCESS_TOKEN_SECRET", "").strip(),
        )
