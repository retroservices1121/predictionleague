from sqlalchemy import Column, Integer, String, JSON, ForeignKey
from sqlalchemy.orm import relationship
from app.database import Base

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    telegram_id = Column(String, unique=True, index=True, nullable=False)
    username = Column(String, nullable=True)

    predictions = relationship("PredictionVote", back_populates="user")


class Prediction(Base):
    __tablename__ = "predictions"
    id = Column(Integer, primary_key=True, index=True)
    question = Column(String, nullable=False)
    options = Column(JSON, nullable=False)
    result = Column(String, nullable=True)

    votes = relationship("PredictionVote", back_populates="prediction")


class PredictionVote(Base):
    __tablename__ = "prediction_votes"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    prediction_id = Column(Integer, ForeignKey("predictions.id"))
    choice = Column(String, nullable=False)

    user = relationship("User", back_populates="predictions")
    prediction = relationship("Prediction", back_populates="votes")
