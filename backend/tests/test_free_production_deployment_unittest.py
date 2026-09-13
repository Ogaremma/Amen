from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class FreeProductionDeploymentTests(unittest.TestCase):
    def test_github_workflow_exists_and_runs_daily_runner(self):
        workflow = ROOT / ".github" / "workflows" / "prediction-daily.yml"
        self.assertTrue(workflow.is_file())
        content = workflow.read_text(encoding="utf-8")
        self.assertIn("cron: \"0 23 * * *\"", content)
        self.assertIn("workflow_dispatch:", content)
        self.assertIn("runs-on: ubuntu-latest", content)
        self.assertIn("Run daily prediction runner on Render", content)
        self.assertIn("curl --fail-with-body", content)
        self.assertIn("/api/v1/admin/prediction-daily/run", content)
        self.assertIn("FOREBET_INGESTION_TOKEN: ${{ secrets.FOREBET_INGESTION_TOKEN }}", content)
        self.assertNotIn("DATABASE_URL: ${{ secrets.DATABASE_URL }}", content)
        self.assertNotIn("TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}", content)
        self.assertNotIn("TELEGRAM_WEBAPP_URL: ${{ secrets.TELEGRAM_WEBAPP_URL }}", content)
        self.assertNotIn("TELEGRAM_WEBHOOK_SECRET", content)

    def test_render_manifest_contains_only_free_web_service(self):
        content = (ROOT / "render.yaml").read_text(encoding="utf-8")
        self.assertIn("type: web", content)
        self.assertIn("name: amen-backend", content)
        self.assertIn("plan: free", content)
        self.assertIn("TELEGRAM_WEBHOOK_SECRET", content)
        self.assertNotIn("type: cron", content)
        self.assertNotIn("type: worker", content)
        self.assertNotIn("amen-prediction-daily", content)
        self.assertNotIn("amen-telegram-bot", content)

    def test_production_container_does_not_start_polling_bot(self):
        dockerfile = (ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("CMD [\"uvicorn\", \"app.main:app\", \"--host\", \"0.0.0.0\", \"--port\", \"8000\"]", dockerfile)
        self.assertNotIn("app.telegram.bot", dockerfile)

        main = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")
        self.assertNotIn("app.telegram.bot", main)
        self.assertNotIn("telegram_bot", main)


if __name__ == "__main__":
    unittest.main()
