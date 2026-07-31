import apiClient from "./apiClient";

export type RegistryDataset = {
  id: string;
  name: string;
  created: string;
  size: string;
};

const getRegistryDatasets = async (limit = 100): Promise<RegistryDataset[]> => {
  const response = await apiClient.get<RegistryDataset[]>("/registryDatasets", {
    params: { limit },
  });
  return response.data;
};

export default getRegistryDatasets;
