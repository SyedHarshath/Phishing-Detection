# Monitoring guide

`python -m mlops.monitoring.monitor` reads the SQLite prediction table, prints aggregate prediction counts, confidence, URL length, and feedback agreement, writes `mlops/monitoring/monitoring_run.json`, and attempts to log to MLflow. Feedback agreement is only agreement with unverified user claims; it is not an accuracy estimate. The command may start a local MLflow server through current configuration and was not run during this audit.

No alert thresholds, drift windows, calibration checks, or scheduled monitoring are configured. Treat outputs as descriptive aggregates, not evidence of model quality. Protect the database and generated reports because raw URLs may be present in the database. Do not publish them without URL redaction/access controls.
