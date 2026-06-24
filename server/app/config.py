from typing import Dict, List
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INSTANT_CLIENT = PROJECT_ROOT / "instantclient_23_0"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env", PROJECT_ROOT / "server" / ".env"),
        env_prefix="DIKAI_",
        extra="ignore",
    )

    HOST: str = "0.0.0.0"
    PORT: int = 8000

    USE_MOCK_DB: bool = True

    DB_HOST: str = ""
    DB_PORT: int = 1521
    DB_SERVICE: str = ""
    DB_USER: str = ""
    DB_PASSWORD: str = ""
    USE_THICK_MODE: bool = True
    ORACLE_INSTANT_CLIENT_PATH: str = (
        str(DEFAULT_INSTANT_CLIENT) if DEFAULT_INSTANT_CLIENT.exists() else ""
    )
    TNS_ADMIN_PATH: str = ""

    DB_POOL_MIN: int = 4
    DB_POOL_MAX: int = 16
    DB_POOL_INCREMENT: int = 1
    DB_POOL_WAIT_SECONDS: int = 10
    DB_STMT_TIMEOUT_SECONDS: int = 30

    CARTON_TABLE: str = "APPS.XXFG_CARTON_MASTER"
    CARTON_BATCH_TABLE: str = "APPS.XXFG_CARTON_BATCH_MASTER"
    CARTON_BATCH_SEQ: str = "APPS.XXFG_CARTON_BATCH_S"

    LPN_PREFIX: str = "LPN-C-"
    LPN_COUNTER_WIDTH: int = 6
    LPN_COUNTER_TABLE: str = "DIKAI_LPN_COUNTER"

    PRINTER_PROTO: str = "TCP"
    PRINTER_IP: str = "192.168.1.110"
    PRINTER_PORT: int = 4916
    PRINTER_TIMEOUT: float = 2.0
    PRINTER_COUNT_STATE_PATH: str = str(PROJECT_ROOT / "server" / "printer_count_state.json")

    AUTH_TOKEN_TTL_SECONDS: int = 8 * 3600

    RATE_LIMIT_PER_SECOND: float = 20.0
    RATE_LIMIT_BURST: int = 40

    IDEMPOTENCY_TTL_SECONDS: int = 24 * 3600
    IDEMPOTENCY_MAX_ENTRIES: int = 10_000

    DEVICES_RAW: str = "stm32-test:test,stm32-line-A-01:dev-secret-1,stm32-line-A-02:dev-secret-2"
    BRANDS_RAW: str = (
        "Monalisa:089:481,"
        "X Monica:063:369,"
        "Alexander:064:370,"
        "X Tiles:093:522,"
        "Venus:062:368"
    )

    @property
    def brands(self) -> List[Dict]:
        out = []
        for piece in self.BRANDS_RAW.split(","):
            p = piece.strip()
            if not p:
                continue
            name, org_code, inv_code = p.split(":")
            out.append({
                "brand": name.strip(),
                "org_code": org_code.strip(),
                "inv_code": int(inv_code.strip()),
            })
        return out

    @property
    def devices(self) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for piece in self.DEVICES_RAW.split(","):
            p = piece.strip()
            if not p:
                continue
            dev, secret = p.split(":")
            out[dev.strip()] = secret.strip()
        return out


settings = Settings()
