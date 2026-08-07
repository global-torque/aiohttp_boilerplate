from typing import Any, cast

from aiohttp import web

from .exceptions import JSONHTTPError, logger_name
from .options import ObjectView


class CreateView(ObjectView):
    partial = False

    def __init__(self, request: web.Request) -> None:
        super().__init__(request)
        self.log = cast(Any, request).log.with_component(logger_name)

        self.data: dict[str, Any] = {}

    async def validate(self, data: dict[str, Any]) -> dict[str, Any]:
        self.log.debug("validate create data", extra={"field_count": len(data)})
        """ Override that method for custom validation
        """
        return data

    async def perform_create(self, data: dict[str, Any]) -> Any:
        self.log.debug("perform create", extra={"field_count": len(data)})
        """ Runs after:
                - successful validation method
                - before_create method
            Calls obj.insert function
        """
        return await self.obj.insert(data=data)

    async def before_create(self, data: dict[str, Any]) -> dict[str, Any]:
        """Runs after:
            - successful validation method
        If you want to change your data before system calls insert method
        Use this method
        """
        return data

    async def after_create(self, data: dict[str, Any]) -> dict[str, Any]:
        """Runs after:
            - successful validation method
            - before_create method
            - perform_create method
        If you need to do anything after object created
        Do it here

        object id is self.obj.id
        """
        return {}

    async def after_create_in_transaction(self, data: dict[str, Any]) -> dict[str, Any]:
        """Override for DB-only work that must commit with the insert."""
        return {}

    async def get_data(self, obj: Any) -> dict[str, Any]:
        """Return id of the object"""
        return {"id": obj.id}

    async def _post(self) -> web.Response:
        """Post method handler, will run one by one
        - on_start
        - get_schema_data/get_data
        - validate
        - before_create
        - perfom_create
        - after_create
        - get_data
        """

        await self.on_start()

        data: dict[str, Any] = {}
        if self.schema:
            data = await self.get_schema_data(partial=self.partial)
        else:
            data = await self.get_request_data(to_json=True)

        data = await self.validate(data)

        if len(data) == 0:
            raise JSONHTTPError(self.request, {"__error__": ["No content"]}, web.HTTPBadRequest)

        self.data.update(await self.before_create(data))
        async with self.obj.sql.transaction():
            self.obj = await self.perform_create(data=self.data)
            self.data.update(await self.after_create_in_transaction(data) or {})
        self.data.update(await self.after_create(data) or {})
        response = await self.get_data(self.obj)
        return self.json_response(response, 201)

    async def post(self) -> web.Response:
        """Post logic is in _post method
        You can do here authentication check before if you need
        """
        return await self._post()
