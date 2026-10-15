# backend/app/tools/base.py :: Tool
class Tool:
    name: ClassVar[str]
    description: ClassVar[str]
    Params: ClassVar[type[BaseModel]]
    # read | write | exec | git | browser | destructive | meta
    category: ClassVar[str] = "read"
    mutates: ClassVar[bool] = False

    async def run(self, ctx: ToolContext, args: BaseModel) -> ToolResult:  # pragma: no cover - interface
        raise NotImplementedError

    @classmethod
    def spec(cls) -> dict:
        schema = cls.Params.model_json_schema()
        schema.pop("title", None)
        for p in schema.get("properties", {}).values():
            p.pop("title", None)
        return {"type": "function", "function": {"name": cls.name, "description": cls.description, "parameters": schema}}
