"""Additive view ownership without constructors or extra HTTP methods."""

from typing import Any, cast

from aiohttp import web

from aiohttp_boilerplate.config import validate_atomic_methods
from aiohttp_boilerplate.transactions import RequestConnectionMixin, http_transaction

from .create import CreateView
from .options import OptionsView
from .update import UpdateView


class AtomicViewMixin(RequestConnectionMixin):
    """Place first on the final registered view; middleware can own the outer scope."""

    atomic_methods: tuple[str, ...] = ("POST", "PUT", "PATCH", "DELETE")
    request: web.Request

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        cls.atomic_methods = validate_atomic_methods(cls.atomic_methods)

    async def _iter(self) -> web.StreamResponse:
        dispatch = cast(Any, super())._iter
        methods = validate_atomic_methods(self.atomic_methods)
        if (
            self.request.method not in methods
            or getattr(self, self.request.method.lower(), None) is None
        ):
            return cast(web.StreamResponse, await dispatch())
        return await http_transaction(self.request, dispatch, kind="view")


class AtomicView(AtomicViewMixin, OptionsView):
    """Custom atomic write handlers; DELETE remains an explicit business handler."""


class AtomicCreateView(AtomicViewMixin, CreateView):
    """The complete create dispatch inside the shared HTTP transaction scope."""


class AtomicUpdateView(AtomicViewMixin, UpdateView):
    """The complete update dispatch inside the shared HTTP transaction scope."""
