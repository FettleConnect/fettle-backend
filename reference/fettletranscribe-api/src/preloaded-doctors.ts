export interface PreloadedDoctor {
  // Keep this stable even if the visible doctor name or department is corrected later.
  seedKey: string;
  name: string;
  department: string;
}

export const PRELOADED_DOCTORS: PreloadedDoctor[] = [
  {
    seedKey: "dr-kishore-b-reddy",
    name: "Dr. Kishore B Reddy",
    department: "Orthopedics Oncology"
  },
  {
    seedKey: "dr-santoshini-nemuri",
    name: "Dr. Santoshini Nemuri",
    department: "Pediatric Orthopedics"
  },
  {
    seedKey: "dr-prathyush-chitithoti",
    name: "Dr. Prathyush Chitithoti",
    department: "Orthopedics"
  },
  {
    seedKey: "dr-sardar-jaideep-singh",
    name: "Dr. Sardar Jaideep Singh",
    department: "Orthopedics"
  },
  {
    seedKey: "dr-shyam-sunder-reddy",
    name: "Dr. Shyam Sunder Reddy",
    department: "Orthopedics"
  },
  {
    seedKey: "dr-imran-ul-haq",
    name: "Dr. Imran Ul Haq",
    department: "Cardiology"
  },
  {
    seedKey: "dr-ravindra-amjuru",
    name: "Dr. Ravindra Amjuru",
    department: "Cardiology"
  },
  {
    seedKey: "dr-k-vikas-dikshit",
    name: "Dr. K. Vikas Dikshit",
    department: "Cardiothoracic Surgery"
  },
  {
    seedKey: "dr-shreya-chaudhari",
    name: "Dr. Shreya Chaudhari",
    department: "Radiology"
  },
  {
    seedKey: "dr-raj-kumar",
    name: "Dr. Raj Kumar",
    department: "Radiology"
  },
  {
    seedKey: "dr-gv-raj-gopal",
    name: "Dr. G.V. Raj Gopal",
    department: "Urology"
  },
  {
    seedKey: "dr-vaibhav-joshi",
    name: "Dr. Vaibhav Joshi",
    department: "Urology"
  },
  {
    seedKey: "dr-vamsi-krishna",
    name: "Dr. Vamsi Krishna",
    department: "General Surgeon"
  },
  {
    seedKey: "dr-ch-tejeswi-das",
    name: "Dr. Ch. Tejeswi Das",
    department: "Plastic Surgery"
  },
  {
    seedKey: "dr-narayan-gaur",
    name: "Dr. Narayan Gaur",
    department: "Pulmonology"
  },
  {
    seedKey: "dr-lingaiah-miryala",
    name: "Dr. Lingaiah Miryala",
    department: "Internal Medicine"
  },
  {
    seedKey: "dr-ravindra-kumar-sravanam",
    name: "Dr. Ravindra Kumar Sravanam",
    department: "Internal Medicine"
  },
  {
    seedKey: "dr-l-sunandhini",
    name: "Dr. L. Sunandhini",
    department: "Internal Medicine"
  },
  {
    seedKey: "dr-shaik-mohd-muzahid",
    name: "Dr. Shaik Mohd Muzahid",
    department: "Neurosurgery"
  },
  {
    seedKey: "dr-neha",
    name: "Dr. Neha",
    department: "Neurology"
  },
  {
    seedKey: "dr-p-pranuthi",
    name: "Dr. P. Pranuthi",
    department: "Neurologist"
  },
  {
    seedKey: "dr-sridevi-k",
    name: "Dr. Sridevi K",
    department: "Gynecology and Obstetrics"
  },
  {
    seedKey: "dr-bindu-kousalya-duddupudi",
    name: "Dr. Bindu Kousalya Duddupudi",
    department: "Gynecology and Obstetrics"
  },
  {
    seedKey: "dr-bharat-patodiya",
    name: "Dr. Bharat Patodiya",
    department: "Medical Oncology"
  },
  {
    seedKey: "dr-kalyan-vangara",
    name: "Dr. Kalyan Vangara",
    department: "Surgical Oncology and General Surgery"
  },
  {
    seedKey: "dr-poojitha-yalla",
    name: "Dr. Poojitha Yalla",
    department: "Surgical Oncology"
  },
  {
    seedKey: "dr-k-shanti-ramesh",
    name: "Dr. K. Shanti Ramesh",
    department: "Radiation Oncology"
  },
  {
    seedKey: "dr-jyothsna",
    name: "Dr. Jyothsna",
    department: "Pediatrics"
  },
  {
    seedKey: "dr-kiran",
    name: "Dr. Kiran",
    department: "Nephrology"
  },
  {
    seedKey: "dr-niranjan",
    name: "Dr. Niranjan",
    department: "Nephrology"
  },
  {
    seedKey: "dr-m-praveen-kumar",
    name: "Dr. M. Praveen Kumar",
    department: "ENT, Head and Neck Surgery"
  },
  {
    seedKey: "dr-g-santosh-kumar",
    name: "Dr. G. Santosh Kumar",
    department: "ENT"
  },
  {
    seedKey: "dr-simran-kaur",
    name: "Dr. Simran Kaur",
    department: "Dermatology"
  },
  {
    seedKey: "dr-maha-lakshmi",
    name: "Dr. Maha Lakshmi",
    department: "Breast and Endocrine Surgery"
  },
  {
    seedKey: "dr-talapalli-santhosh",
    name: "Dr. Talapalli Santhosh",
    department: "Maxillofacial Surgery"
  },
  {
    seedKey: "dr-g-saranya",
    name: "Dr. G. Saranya",
    department: "Dental"
  },
  {
    seedKey: "dr-anitha-rayirala",
    name: "Dr. Anitha Rayirala",
    department: "Psychiatry"
  },
  {
    seedKey: "dr-narendra-gogireddy",
    name: "Dr. Narendra Gogireddy",
    department: "Surgical Gastroenterology and General Surgery"
  },
  {
    seedKey: "dr-harsha-vardhan",
    name: "Dr. Harsha Vardhan",
    department: "Ophthalmology"
  },
  {
    seedKey: "dr-m-chandra-shekar",
    name: "Dr. M. Chandra Shekar",
    department: "Pediatrics"
  },
  {
    seedKey: "dr-sriram",
    name: "Dr. Sriram",
    department: "Medical Gastroenterologist"
  }
];

export function slugifyPathSegment(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/&/g, " and ")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .replace(/-{2,}/g, "-");
}
