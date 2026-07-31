import { type AxiosProgressEvent } from "axios";
import apiClient from "./apiClient";
import { type RegistryDataset } from "./getRegistryDatasets";

const uploadDataset = async (
  file: File,
  onUploadProgress?: (event: AxiosProgressEvent) => void,
): Promise<RegistryDataset> => {
  const formData = new FormData();
  formData.append("file", file);

  const response = await apiClient.post<RegistryDataset>(
    "/datasets/upload",
    formData,
    {
      headers: { "Content-Type": "multipart/form-data" },
      onUploadProgress,
    },
  );
  return response.data;
};

export default uploadDataset;
