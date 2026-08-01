import apiClient from "./apiClient";

export type ScoringDataVersion = {
  version_id: string;
  created_at: string;
  label: string;
  is_latest: boolean;
};

const getScoringDataVersions = async (
  activeDatasetId?: string,
): Promise<ScoringDataVersion[]> => {
  const params = activeDatasetId ? { active_dataset_id: activeDatasetId } : {};
  const response = await apiClient.get<ScoringDataVersion[]>(
    "/scoringDataVersions",
    { params },
  );
  return response.data;
};

export default getScoringDataVersions;
