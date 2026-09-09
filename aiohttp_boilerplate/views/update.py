from typing import Any, cast

from aiohttp import web

from .exceptions import JSONHTTPError, logger_name
from .options import ObjectView


class UpdateView(ObjectView):

    def __init__(self, request: web.Request) -> None:
        super().__init__(request)
        self.log = cast(Any, request).log.with_component(logger_name)

        # Can we update a part of schema data
        self.partial = True
        self.where = ""
        self.params: dict[str, Any] = {}
        self.data: dict[str, Any] = {}

    async def validate(self, data: dict[str, Any]) -> dict[str, Any]:
        self.log.debug("validate update data", extra={"field_count": len(data)})
        """ Override that method for custom validation
        """
        return data

    async def perform_update(self, where: str, params: dict[str, Any], data: dict[str, Any]) -> int:
        self.log.debug(
            "perform update",
            extra={"parameter_count": len(params), "field_count": len(data)},
        )
        """ Runs after:
                - successful validation method
                - before_update method
            Calls obj.update function
        """
        return cast(int, await self.obj.update(where, params, data))

    async def before_update(self, data: dict[str, Any]) -> dict[str, Any]:
        self.log.debug("before update", extra={"field_count": len(data)})
        """ Runs after:
                - successful validation method
            If you want to change your data before system calls insert method
            Use this method

            Return an empty mapping when the validated request is already
            reflected by the current object and no update is required.
        """
        return data

    async def after_update(self, data: dict[str, Any]) -> dict[str, Any]:
        self.log.debug("after update", extra={"field_count": len(data)})
        """ Runs after:
                - successful validation method
                - before_create method
                - perfom_create method
            If you need to do anything after object updated
            Do it here

            new object data is self.obj
        """
        return data

    async def after_update_in_transaction(self, data: dict[str, Any]) -> dict[str, Any]:
        """Override for DB-only work that must commit with the update."""
        return {}

    async def _patch(self) -> web.Response:
        """Post method handler, will run one by one
        - on_start
        - get_schema_data/get_data
        - validate
        - before_update
        - perfomupdate
        - after_update
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

        self.data.update(await self.before_update(data))
        if self.data:
            async with self.obj.sql.transaction():
                updated = await self.perform_update(
                    where=self.where,
                    params=self.params,
                    data=self.data,
                )
                if updated == 0:
                    raise JSONHTTPError(
                        self.request, {"__error__": ["No object updated"]}, web.HTTPNotFound
                    )
                self.data.update(await self.after_update_in_transaction(data) or {})
            self.data.update(await self.after_update(data) or {})
        response = await self.get_data(self.obj)
        return self.json_response(response)

    async def _put(self) -> web.Response:
        self.partial = False
        return await self._patch()

    async def patch(self) -> web.Response:
        return await self._patch()

    async def put(self) -> web.Response:
        return await self._put()
