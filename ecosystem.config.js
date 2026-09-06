module.exports = {
  apps: [
    {
      name: "whatsapp-bot-api",
      script: "start.py", // Or "run_workers.py" if you want multi-process uvicorn
      interpreter: "python3", // Path to python or virtualenv (e.g., "./venv/bin/python3")
      // args: "8000 0.0.0.0", // Optional port and host arguments
      autorestart: true,
      restart_delay: 3000,
      max_memory_restart: "1G",
      watch: false,
      env: {
        NODE_ENV: "production",
        PYTHONUNBUFFERED: "1"
      }
    },
    {
      name: "whatsapp-followup-worker",
      script: "src/workers/follow_up_worker.py",
      interpreter: "python3", // Path to python or virtualenv (e.g., "./venv/bin/python3")
      autorestart: true,
      restart_delay: 5000,
      max_memory_restart: "500M",
      watch: false,
      env: {
        NODE_ENV: "production",
        PYTHONUNBUFFERED: "1"
      }
    }
  ]
};
