import apiClient from "./apiClient";
import { type ScoringData } from "~/state/AppState";

const getScoringData = async (
  activeDatasetId?: string,
  versionId?: string,
): Promise<ScoringData[]> => {
  const params = {
    ...(activeDatasetId ? { active_dataset_id: activeDatasetId } : {}),
    ...(versionId ? { version_id: versionId } : {}),
  };
  const response = await apiClient.get<ScoringData[]>("/scoringData", {
    params,
  });
  return response.data;
};

export default getScoringData;
