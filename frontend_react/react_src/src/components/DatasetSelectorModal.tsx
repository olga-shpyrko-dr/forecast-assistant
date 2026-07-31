import { useContext, useEffect, useState } from "react";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { faDatabase } from "@fortawesome/free-solid-svg-icons/faDatabase";
import { Button } from "~/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "~/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "~/components/ui/select";
import { Alert, AlertDescription } from "~/components/ui/alert";
import { Label } from "~/components/ui/label";
import { Input } from "~/components/ui/input";
import useRegistryDatasets from "~/hooks/useRegistryDatasets";
import uploadDataset from "~/api/uploadDataset";
import { AppStateContext } from "~/state/AppState";

// Sentinel for the deploy-time scoring dataset (radix Select disallows empty values).
const DEFAULT_VALUE = "__default__";

const DatasetSelectorModal = () => {
  const { activeDatasetId, setActiveDataset } = useContext(AppStateContext);
  const {
    datasets,
    loading,
    error: listError,
    refetch,
  } = useRegistryDatasets();

  const [isOpen, setIsOpen] = useState(false);
  const [selectedId, setSelectedId] = useState(DEFAULT_VALUE);
  const [file, setFile] = useState<File | null>(null);
  const [isPending, setIsPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Reset the form to the currently active selection whenever the modal opens.
  useEffect(() => {
    if (isOpen) {
      setSelectedId(activeDatasetId || DEFAULT_VALUE);
      setFile(null);
      setError(null);
    }
  }, [isOpen, activeDatasetId]);

  const onSave = async () => {
    setIsPending(true);
    setError(null);
    try {
      if (file) {
        const uploaded = await uploadDataset(file);
        await refetch();
        setActiveDataset(uploaded.id, uploaded.name);
      } else if (selectedId === DEFAULT_VALUE) {
        setActiveDataset("", "");
      } else {
        const dataset = datasets.find((d) => d.id === selectedId);
        setActiveDataset(selectedId, dataset?.name ?? selectedId);
      }
      setIsOpen(false);
    } catch (err) {
      console.error("Error updating scoring dataset", err);
      setError(
        file
          ? "Failed to upload the file to the Data Registry."
          : "Failed to update the scoring dataset.",
      );
    } finally {
      setIsPending(false);
    }
  };

  return (
    <Dialog open={isOpen} onOpenChange={setIsOpen}>
      <DialogTrigger asChild>
        <Button variant="outline">
          <FontAwesomeIcon icon={faDatabase} className="mr-2" />
          Change dataset
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Scoring dataset</DialogTitle>
          <DialogDescription>
            Select a dataset from the Data Registry or upload a new file to
            forecast against it.
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            <Label>Data Registry</Label>
            <Select
              value={selectedId}
              onValueChange={setSelectedId}
              disabled={!!file || loading}
            >
              <SelectTrigger>
                <SelectValue
                  placeholder={
                    loading ? "Loading datasets…" : "Select a dataset"
                  }
                />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={DEFAULT_VALUE}>
                  Default (configured scoring dataset)
                </SelectItem>
                {datasets.map((dataset) => (
                  <SelectItem key={dataset.id} value={dataset.id}>
                    <div className="flex w-full items-center justify-between gap-4">
                      <span>{dataset.name}</span>
                      <span className="text-muted-foreground">
                        {dataset.size}
                      </span>
                    </div>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="flex flex-col gap-2">
            <Label htmlFor="dataset-upload">Or upload a file</Label>
            <Input
              id="dataset-upload"
              type="file"
              accept=".csv,.xlsx,.xls,.parquet"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
          </div>

          {(error || listError) && (
            <Alert variant="destructive">
              <AlertDescription>{error || listError}</AlertDescription>
            </Alert>
          )}
        </div>

        <DialogFooter>
          <Button
            variant="ghost"
            onClick={() => setIsOpen(false)}
            disabled={isPending}
          >
            Cancel
          </Button>
          <Button onClick={onSave} disabled={isPending}>
            {isPending ? "Saving…" : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default DatasetSelectorModal;
