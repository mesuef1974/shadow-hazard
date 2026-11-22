import { defineRailway, github, project, service } from "railway/iac";
// This repository manages only its own resources in the environment. Other
// repositories export their own partial name.
// See https://docs.railway.com/infrastructure-as-code#multi-repo-projects
export const partial = "shadow-hazard";
export default defineRailway(() => {
  const shadowHazard = service("shadow-hazard", {
    source: github("mesuef1974/shadow-hazard", { branch: "main", checkSuites: false }),
    build: { buildEnvironment: "V3", builder: "DOCKERFILE", dockerfilePath: "Dockerfile" },
    healthcheck: "/healthz/",
    healthcheckTimeout: 180,
    deploy: { restartPolicyType: "ON_FAILURE" },
    replicas: { "europe-west4-drams3a": 1 },
    networking: { privateNetworkEndpoint: "sima" },
  });

  return project("shadow-hazard", {
    variables: { managed: false },
    resources: [shadowHazard],
  });
});
