"""
Integration tests for the SwimCoach API.

These tests use FastAPI's TestClient to test the API endpoints
with mock services (no external dependencies). They verify the
full request/response cycle works correctly.

Running integration tests:
    pytest tests/integration/ -v

The tests use mock mode for all external services:
- MockStorageClient for R2
- MockSessionRepository for Snowflake
- SwimCoach with a mock Anthropic client
"""

import base64
import pytest
from fastapi.testclient import TestClient
from uuid import uuid4

# Set environment to use mocks before importing app
import os
os.environ["SNOWFLAKE_MOCK_MODE"] = "true"
os.environ["R2_MOCK_MODE"] = "true"
os.environ["VIDEO_PROCESSOR_MOCK_MODE"] = "true"
os.environ["API_KEYS"] = "test-api-key"
os.environ["ANTHROPIC_API_KEY"] = "test-key"
# Treat the test key as trusted so rate limiting doesn't trip across many analyses
os.environ["RATE_LIMIT_BYPASS_KEYS"] = "test-api-key"

from src.main import app


@pytest.fixture
def client():
    """Create a test client for the API."""
    return TestClient(app)


@pytest.fixture
def api_key():
    """Valid API key for tests."""
    return "test-api-key"


@pytest.fixture
def mock_frame():
    """Create a minimal valid JPEG for testing."""
    # 1x1 red pixel JPEG (smallest valid JPEG)
    return bytes([
        0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10, 0x4A, 0x46,
        0x49, 0x46, 0x00, 0x01, 0x01, 0x00, 0x00, 0x01,
        0x00, 0x01, 0x00, 0x00, 0xFF, 0xDB, 0x00, 0x43,
        0x00, 0x08, 0x06, 0x06, 0x07, 0x06, 0x05, 0x08,
        0x07, 0x07, 0x07, 0x09, 0x09, 0x08, 0x0A, 0x0C,
        0x14, 0x0D, 0x0C, 0x0B, 0x0B, 0x0C, 0x19, 0x12,
        0x13, 0x0F, 0x14, 0x1D, 0x1A, 0x1F, 0x1E, 0x1D,
        0x1A, 0x1C, 0x1C, 0x20, 0x24, 0x2E, 0x27, 0x20,
        0x22, 0x2C, 0x23, 0x1C, 0x1C, 0x28, 0x37, 0x29,
        0x2C, 0x30, 0x31, 0x34, 0x34, 0x34, 0x1F, 0x27,
        0x39, 0x3D, 0x38, 0x32, 0x3C, 0x2E, 0x33, 0x34,
        0x32, 0xFF, 0xC0, 0x00, 0x0B, 0x08, 0x00, 0x01,
        0x00, 0x01, 0x01, 0x01, 0x11, 0x00, 0xFF, 0xC4,
        0x00, 0x1F, 0x00, 0x00, 0x01, 0x05, 0x01, 0x01,
        0x01, 0x01, 0x01, 0x01, 0x00, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x00, 0x01, 0x02, 0x03, 0x04,
        0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B, 0xFF,
        0xC4, 0x00, 0xB5, 0x10, 0x00, 0x02, 0x01, 0x03,
        0x03, 0x02, 0x04, 0x03, 0x05, 0x05, 0x04, 0x04,
        0x00, 0x00, 0x01, 0x7D, 0x01, 0x02, 0x03, 0x00,
        0x04, 0x11, 0x05, 0x12, 0x21, 0x31, 0x41, 0x06,
        0x13, 0x51, 0x61, 0x07, 0x22, 0x71, 0x14, 0x32,
        0x81, 0x91, 0xA1, 0x08, 0x23, 0x42, 0xB1, 0xC1,
        0x15, 0x52, 0xD1, 0xF0, 0x24, 0x33, 0x62, 0x72,
        0x82, 0x09, 0x0A, 0x16, 0x17, 0x18, 0x19, 0x1A,
        0x25, 0x26, 0x27, 0x28, 0x29, 0x2A, 0x34, 0x35,
        0x36, 0x37, 0x38, 0x39, 0x3A, 0x43, 0x44, 0x45,
        0x46, 0x47, 0x48, 0x49, 0x4A, 0x53, 0x54, 0x55,
        0x56, 0x57, 0x58, 0x59, 0x5A, 0x63, 0x64, 0x65,
        0x66, 0x67, 0x68, 0x69, 0x6A, 0x73, 0x74, 0x75,
        0x76, 0x77, 0x78, 0x79, 0x7A, 0x83, 0x84, 0x85,
        0x86, 0x87, 0x88, 0x89, 0x8A, 0x92, 0x93, 0x94,
        0x95, 0x96, 0x97, 0x98, 0x99, 0x9A, 0xA2, 0xA3,
        0xA4, 0xA5, 0xA6, 0xA7, 0xA8, 0xA9, 0xAA, 0xB2,
        0xB3, 0xB4, 0xB5, 0xB6, 0xB7, 0xB8, 0xB9, 0xBA,
        0xC2, 0xC3, 0xC4, 0xC5, 0xC6, 0xC7, 0xC8, 0xC9,
        0xCA, 0xD2, 0xD3, 0xD4, 0xD5, 0xD6, 0xD7, 0xD8,
        0xD9, 0xDA, 0xE1, 0xE2, 0xE3, 0xE4, 0xE5, 0xE6,
        0xE7, 0xE8, 0xE9, 0xEA, 0xF1, 0xF2, 0xF3, 0xF4,
        0xF5, 0xF6, 0xF7, 0xF8, 0xF9, 0xFA, 0xFF, 0xDA,
        0x00, 0x08, 0x01, 0x01, 0x00, 0x00, 0x3F, 0x00,
        0xFB, 0xD3, 0x28, 0xA0, 0x02, 0x8A, 0x28, 0x03,
        0xFF, 0xD9
    ])


# ---------------------------------------------------------------------------
# Health Check Tests
# ---------------------------------------------------------------------------

class TestHealthEndpoints:
    """Tests for health check endpoints."""
    
    def test_health_check_returns_ok(self, client):
        """GET /health should return healthy status."""
        response = client.get("/health")
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
    
    def test_root_returns_api_info(self, client):
        """GET / should return API metadata."""
        response = client.get("/")
        
        assert response.status_code == 200
        data = response.json()
        assert "SwimCoach" in data["message"]
        assert "docs" in data


# ---------------------------------------------------------------------------
# Frame Upload Tests
# ---------------------------------------------------------------------------

class TestFrameUpload:
    """Tests for frame upload endpoint."""
    
    def test_upload_frames_requires_api_key(self, client, mock_frame):
        """POST /api/v1/analysis/upload should require API key."""
        response = client.post(
            "/api/v1/analysis/upload",
            files=[("frames", ("frame1.jpg", mock_frame, "image/jpeg"))],
        )
        
        assert response.status_code == 403
    
    def test_upload_frames_success(self, client, api_key, mock_frame):
        """POST /api/v1/analysis/upload should accept valid frames."""
        response = client.post(
            "/api/v1/analysis/upload",
            files=[
                ("frames", ("frame1.jpg", mock_frame, "image/jpeg")),
                ("frames", ("frame2.jpg", mock_frame, "image/jpeg")),
                ("frames", ("frame3.jpg", mock_frame, "image/jpeg")),
            ],
            data={"stroke_type": "freestyle"},
            headers={"X-API-Key": api_key},
        )
        
        assert response.status_code == 201
        data = response.json()
        assert "session_id" in data
        assert data["frames_received"] == 3
    
    def test_upload_frames_rejects_empty(self, client, api_key):
        """POST /api/v1/analysis/upload should reject zero frames."""
        response = client.post(
            "/api/v1/analysis/upload",
            files=[],
            headers={"X-API-Key": api_key},
        )
        
        # FastAPI returns 422 for validation errors
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# Session Retrieval Tests
# ---------------------------------------------------------------------------

class TestSessionRetrieval:
    """Tests for session retrieval endpoint."""
    
    def test_get_session_not_found(self, client, api_key):
        """GET /api/v1/sessions/{id} should return 404 for unknown session."""
        fake_id = str(uuid4())
        response = client.get(
            f"/api/v1/sessions/{fake_id}",
            headers={"X-API-Key": api_key},
        )
        
        assert response.status_code == 404
    
    def test_get_session_requires_api_key(self, client):
        """GET /api/v1/sessions/{id} should require API key."""
        fake_id = str(uuid4())
        response = client.get(f"/api/v1/sessions/{fake_id}")
        
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Happy Path Integration Test
# ---------------------------------------------------------------------------

class TestFullAnalysisFlow:
    """End-to-end test for the complete analysis flow."""
    
    def test_upload_and_analyze_flow(self, client, api_key, mock_frame):
        """
        Test the complete happy path:
        1. Upload frames
        2. Request analysis
        3. Verify response structure
        
        Note: Analysis requires a real Anthropic API call, so we just
        test the upload step in mock mode. Full analysis testing would
        need a mock Anthropic client.
        """
        # Step 1: Upload frames
        upload_response = client.post(
            "/api/v1/analysis/upload",
            files=[
                ("frames", ("frame1.jpg", mock_frame, "image/jpeg")),
                ("frames", ("frame2.jpg", mock_frame, "image/jpeg")),
            ],
            data={"stroke_type": "freestyle", "user_notes": "Working on my catch"},
            headers={"X-API-Key": api_key},
        )
        
        assert upload_response.status_code == 201
        session_id = upload_response.json()["session_id"]
        
        # Step 2: Verify session was created
        # Note: MockSessionRepository might not persist, so we just verify
        # the upload response structure is correct
        assert session_id is not None
        
        # In a full integration test with mocked Anthropic:
        # analyze_response = client.post(
        #     f"/api/v1/analysis/{session_id}/analyze",
        #     json={"stroke_type": "freestyle"},
        #     headers={"X-API-Key": api_key},
        # )
        # assert analyze_response.status_code == 200
        # assert "summary" in analyze_response.json()


# ---------------------------------------------------------------------------
# Async Analysis Job Tests
# ---------------------------------------------------------------------------

from src.api.dependencies import get_swim_coach
from src.core.analysis.models import (
    AnalysisResult,
    CoachingFeedback,
    FeedbackPriority,
    StrokeType,
    TechniqueCategory,
    TechniqueObservation,
)


class _FakeCoach:
    """Stand-in for SwimCoach so tests don't call the real Anthropic API."""

    async def analyze_video(self, frames, stroke_type=StrokeType.FREESTYLE, user_notes="", knowledge_context=None):
        return AnalysisResult(
            stroke_type=stroke_type,
            summary="SUMMARY: Solid freestyle.\nPRIMARY FOCUS: Improve the catch.",
            feedback=[
                CoachingFeedback(
                    priority=FeedbackPriority.PRIMARY,
                    observation=TechniqueObservation(
                        category=TechniqueCategory.CATCH_AND_PULL,
                        description="Elbow drops during the catch",
                    ),
                    recommendation="Lead with your elbow — early vertical forearm",
                    drill_suggestions=["catch-up drill"],
                )
            ],
        )


class _FailingCoach:
    async def analyze_video(self, *args, **kwargs):
        raise RuntimeError("boom from claude")


def _upload(client, api_key, mock_frame, count=2):
    files = [("frames", (f"frame{i}.jpg", mock_frame, "image/jpeg")) for i in range(count)]
    res = client.post(
        "/api/v1/analysis/upload",
        files=files,
        data={"stroke_type": "freestyle"},
        headers={"X-API-Key": api_key},
    )
    assert res.status_code == 201
    return res.json()["session_id"]


class TestAsyncAnalysisFlow:
    """The new background-job analysis path (poll GET session for the result)."""

    def test_session_pending_before_analysis(self, client, api_key, mock_frame):
        """A freshly uploaded session reports pending and is not analyzed."""
        session_id = _upload(client, api_key, mock_frame)

        res = client.get(f"/api/v1/sessions/{session_id}", headers={"X-API-Key": api_key})
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "pending"
        assert data["is_analyzed"] is False

    def test_analyze_returns_202_and_completes(self, client, api_key, mock_frame):
        """POST analyze returns 202; the background job persists feedback."""
        app.dependency_overrides[get_swim_coach] = lambda: _FakeCoach()
        try:
            session_id = _upload(client, api_key, mock_frame)

            res = client.post(
                f"/api/v1/analysis/{session_id}/analyze",
                json={"stroke_type": "freestyle", "user_notes": "catch help"},
                headers={"X-API-Key": api_key},
            )
            assert res.status_code == 202
            assert res.json()["status"] == "processing"

            # TestClient runs background tasks before returning, so it's done now.
            detail = client.get(f"/api/v1/sessions/{session_id}", headers={"X-API-Key": api_key})
            assert detail.status_code == 200
            data = detail.json()
            assert data["status"] == "complete"
            assert data["is_analyzed"] is True
            assert data["stroke_type"] == "freestyle"
            assert "freestyle" in data["summary"].lower() or "catch" in data["summary"].lower()
            assert len(data["feedback"]) >= 1
            assert data["feedback"][0]["priority"] == "primary"
        finally:
            app.dependency_overrides.pop(get_swim_coach, None)

    def test_analyze_failure_surfaces_as_failed(self, client, api_key, mock_frame):
        """A crashing analysis records status=failed with an error message."""
        app.dependency_overrides[get_swim_coach] = lambda: _FailingCoach()
        try:
            session_id = _upload(client, api_key, mock_frame)

            res = client.post(
                f"/api/v1/analysis/{session_id}/analyze",
                json={"stroke_type": "freestyle"},
                headers={"X-API-Key": api_key},
            )
            assert res.status_code == 202

            detail = client.get(f"/api/v1/sessions/{session_id}", headers={"X-API-Key": api_key})
            data = detail.json()
            assert data["status"] == "failed"
            assert data["error"] and "boom" in data["error"]
            assert data["is_analyzed"] is False
        finally:
            app.dependency_overrides.pop(get_swim_coach, None)

    def test_analyze_unknown_session_404(self, client, api_key):
        """Analyzing a session that doesn't exist returns 404."""
        res = client.post(
            f"/api/v1/analysis/{uuid4()}/analyze",
            json={"stroke_type": "freestyle"},
            headers={"X-API-Key": api_key},
        )
        assert res.status_code == 404
