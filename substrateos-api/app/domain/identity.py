from pydantic import BaseModel, ConfigDict


class User(BaseModel):
    model_config = ConfigDict(frozen=False)

    user_id: str
    tenant_id: str
    email: str
    display_name: str
    group_ids: set[str]
    manager_id: str | None = None

    def principals(self) -> set[str]:
        return {self.user_id, *self.group_ids}
