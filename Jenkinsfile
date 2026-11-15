// AI Reliability Lab — official CI pipeline (Jenkins; no GitHub Actions).
//
// The default build is fully OFFLINE and CPU-only: every evaluation uses deterministic
// mock/scripted systems. No GPU, CUDA, model API, paid judge, cloud service or external
// database is needed. The optional external stage runs only when RUN_EXTERNAL=true and
// reads credentials from the Jenkins credential store (never from this file or Git).
//
// Agent requirements: Linux, git, uv (https://docs.astral.sh/uv/), optional docker.

pipeline {
  agent any

  options {
    disableConcurrentBuilds()          // 16 GB laptop: one build at a time
    timeout(time: 45, unit: 'MINUTES')
    timestamps()
    buildDiscarder(logRotator(numToKeepStr: '20', artifactNumToKeepStr: '10'))
  }

  parameters {
    string(name: 'SUITE', defaultValue: 'ci_core', description: 'Suite for the optional external evaluation')
    string(name: 'SYSTEM_CONFIG', defaultValue: 'configs/systems/openai-compatible-example.yaml',
           description: 'System config for the optional external evaluation')
    string(name: 'BASELINE_RUN', defaultValue: '', description: 'Baseline run ID or baseline:<name> (optional)')
    booleanParam(name: 'RUN_EXTERNAL', defaultValue: false,
                 description: 'Run the external-endpoint evaluation (needs Jenkins credentials)')
    string(name: 'EXTERNAL_CREDENTIALS_ID', defaultValue: 'llm-provider-api-key',
           description: 'Jenkins "Secret text" credential ID exported as the system api_key_env')
    string(name: 'EXTERNAL_API_KEY_ENV', defaultValue: 'OPENAI_API_KEY',
           description: 'Environment variable name the system config reads (api_key_env)')
  }

  environment {
    UV_NO_PROGRESS = '1'
    PYTHONDONTWRITEBYTECODE = '1'
    PYTEST_ADDOPTS = '-p no:cacheprovider'
    // Guarantee offline behaviour of the default pipeline.
    OPENAI_API_KEY = ''
    ANTHROPIC_API_KEY = ''
  }

  stages {
    stage('Checkout') {
      steps {
        checkout scm
        sh 'git log -1 --format="%h %an %s"'
      }
    }

    stage('Environment Validation') {
      steps {
        sh 'uv --version'
        sh 'uv sync --frozen --all-extras'
        sh 'uv run aireliability doctor'
      }
    }

    stage('Dependency Lock Check') {
      steps { sh 'uv lock --check' }
    }

    stage('Formatting') {
      steps { sh 'uv run ruff format --check src tests' }
    }

    stage('Ruff') {
      steps { sh 'uv run ruff check src tests' }
    }

    stage('Strict mypy') {
      steps { sh 'uv run mypy' }
    }

    stage('Unit Tests') {
      steps { sh 'uv run pytest tests/unit -q --junitxml=build/junit-unit.xml --cov --cov-report=xml:build/coverage.xml' }
    }

    stage('Property Tests') {
      steps { sh 'uv run pytest tests/property -q --junitxml=build/junit-property.xml' }
    }

    stage('Integration Tests') {
      steps { sh 'uv run pytest tests/integration -q --junitxml=build/junit-integration.xml' }
    }

    stage('Security Evaluation Tests') {
      steps { sh 'uv run pytest tests/security -q --junitxml=build/junit-security.xml' }
    }

    stage('Mock Model E2E') {
      steps {
        sh 'uv run aireliability run configs/runs/ci-core.yaml --root . --fail-on-gate'
        sh 'uv run pytest tests/e2e -q -k "cli or mock" --junitxml=build/junit-e2e-mock.xml'
      }
    }

    stage('Regression Gate E2E') {
      // The demo candidate must be REJECTED: improved correctness, regressed injection safety.
      steps { sh 'uv run pytest tests/e2e -q -k release_gate --junitxml=build/junit-e2e-gate.xml' }
    }

    stage('Incident-to-Eval E2E') {
      steps { sh 'uv run pytest tests/e2e -q -k incident --junitxml=build/junit-e2e-incident.xml' }
    }

    stage('Secret Scan') {
      steps {
        sh 'git ls-files -z | xargs -0 uv run detect-secrets-hook --baseline .secrets.baseline'
      }
    }

    stage('Package Build') {
      steps {
        sh 'rm -rf dist && uv build'
        sh 'ls -l dist'
      }
    }

    stage('Docker Build') {
      when { expression { return sh(script: 'command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1', returnStatus: true) == 0 } }
      steps {
        sh "docker build -t ai-reliability-lab:${env.BUILD_NUMBER} ."
        sh "docker run --rm ai-reliability-lab:${env.BUILD_NUMBER} --version"
      }
    }

    stage('External Evaluation (optional)') {
      when { expression { return params.RUN_EXTERNAL } }
      steps {
        withCredentials([string(credentialsId: params.EXTERNAL_CREDENTIALS_ID, variable: 'EXTERNAL_API_KEY')]) {
          // The key is exported under the variable name the system config expects; it is
          // never echoed, written to disk or stored in results.
          sh '''
            set +x
            export "${EXTERNAL_API_KEY_ENV}=${EXTERNAL_API_KEY}"
            cat > build/external-run.yaml <<EOF
system: ${SYSTEM_CONFIG}
suite: ${SUITE}
repetitions: 3
gates: configs/gates/release.yaml
output_dir: results
labels: {pipeline: jenkins-external, build: "${BUILD_NUMBER}"}
EOF
            if [ -n "${BASELINE_RUN}" ]; then echo "baseline: ${BASELINE_RUN}" >> build/external-run.yaml; fi
            uv run aireliability run build/external-run.yaml --root . --fail-on-gate
          '''
        }
      }
    }
  }

  post {
    always {
      junit allowEmptyResults: true, testResults: 'build/junit-*.xml'
      // Reports and summaries only: no raw traces, no checkpoints, no Parquet with model text.
      archiveArtifacts allowEmptyArchive: true, fingerprint: true,
        artifacts: 'results/*/report.md, results/*/release-report.md, results/*/summary.json, results/*/gates.json, results/*/manifest.json, results/*/plots/*.svg, dist/*'
    }
    cleanup {
      sh 'rm -rf build/external-run.yaml'
    }
  }
}
