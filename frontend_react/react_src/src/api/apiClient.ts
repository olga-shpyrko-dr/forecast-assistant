import axios from "axios";

let backendUrl = "http://localhost:8080";

if (import.meta.env.MODE === "production") {
  // expected valid url https://app.datarobot.com/custom_applications/{appId}/fastapi
  const fullUrl = window.location.origin + window.location.pathname;
  backendUrl = fullUrl.split("/").splice(0, 5).join("/");
}

const apiClient = axios.create({
  baseURL: backendUrl,
  headers: {
    Accept: "application/json",
    "Content-type": "application/json",
  },
  withCredentials: true,
});

export default apiClient;

const drClient = axios.create({
  baseURL: window.ENV?.DATAROBOT_ENDPOINT || `${window.location.origin}/api/v2`,
  headers: {
    Accept: "application/json",
    "Content-type": "application/json",
  },
  withCredentials: true,
});

export { drClient, apiClient };
