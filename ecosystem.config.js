module.exports = {
  apps: [
    {
      name: "bot-api",
      script: "start.py",
      interpreter: "python3",
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
      script: "src/workers/whatsapp_followup_worker.py",
      interpreter: "python3",
      autorestart: true,
      restart_delay: 5000,
      max_memory_restart: "500M",
      watch: false,
      env: {
        NODE_ENV: "production",
        PYTHONUNBUFFERED: "1"
      }
    },
    {
      name: "instagram-followup-worker",
      script: "src/workers/instagram_followup_worker.py",
      interpreter: "python3",
      autorestart: true,
      restart_delay: 5000,
      max_memory_restart: "500M",
      watch: false,
      env: {
        NODE_ENV: "production",
        PYTHONUNBUFFERED: "1"
      }
    },
    {
      name: "facebook-followup-worker",
      script: "src/workers/facebook_followup_worker.py",
      interpreter: "python3",
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
