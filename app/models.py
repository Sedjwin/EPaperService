"""SQLAlchemy model for display bookings."""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class Booking(Base):
    __tablename__ = "bookings"

    booking_id:     Mapped[str]  = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    principal_id:   Mapped[int]  = mapped_column(Integer, nullable=False)   # UserManager user_id
    principal_name: Mapped[str]  = mapped_column(String(128), nullable=False)
    principal_type: Mapped[str]  = mapped_column(String(16), nullable=False)  # "human" | "agent"
    content_type:   Mapped[str]  = mapped_column(String(16), nullable=False)  # "markdown" | "svg" | "image"
    content:        Mapped[str]  = mapped_column(Text, nullable=False)        # raw text or base64
    start_time:     Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_time:       Mapped[datetime] = mapped_column(DateTime, nullable=False)
    description:    Mapped[str]  = mapped_column(String(256), nullable=False, default="")
    cancelled:      Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cancelled_by:   Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at:     Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
